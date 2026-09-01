"""The backend-agnostic reconciliation loop.

Nothing here knows what a Terraform module or a docker-compose file is —
that's `backends.py`'s job. This module only knows: pull git, take a lock,
call `apply()`, record what happened, and tell someone if it failed.
"""

from __future__ import annotations

import fcntl
import json
import logging
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, Field

from .backends import BackEnd
from .models import ApplyResult, Status

logger = logging.getLogger("gitops_reconciler.wrapper")

STATE_DIR = Path("/var/lib/gitops-agent")
LOCK_DIR = Path("/var/run/gitops-agent")

Notifier = Callable[[str, Status], None]


def default_notifier(target_name: str, status: Status) -> None:
    """Default notification handler for failed reconciliations.

    Logs errors to the logger but takes no other action. Override this by passing
    a custom Notifier to ManagedTarget to integrate with external notification systems
    (Slack, PagerDuty, email, etc.).

    Only called on FAILED results - successful reconciliations are not notified by default.

    Args:
        target_name: Name of the target that failed
        status: Status object with FAILED result and error message
    """
    logger.error("RECONCILE FAILED for %s: %s", target_name, status.message)


class ManagedTarget(BaseModel):
    """One reconciliation target: a named backend tied to a git repository.

    Each target gets its own lock file, state file, and notification handler.
    The wrapper uses these to coordinate reconciliation across multiple targets
    without interference.

    IMPORTANT: `name` must be globally unique across all targets you manage.
    It serves as both the lock key and state file key. For multiple instances
    of the same backend type (e.g., multiple Raspberry Pis), compose uniqueness
    at the call site: `name=f"pi-{hostname}"`.

    Attributes:
        name: Unique identifier for this target (used for locks and state)
        backend: The backend implementation (Terraform, Pulumi, Compose, etc.)
        repo: Path to the git repository containing desired state
        notify: Optional custom notification handler (default: logs to stderr)

    Example:
        ```python
        from gitops_reconciler.backends import build_backend
        from gitops_reconciler.models import TerraformConfig
        from gitops_reconciler.wrapper import ManagedTarget, tick

        config = TerraformConfig(workdir=Path("/srv/infra/terraform"))
        backend = build_backend(config)
        target = ManagedTarget(
            name="prod-infra",
            backend=backend,
            repo=Path("/srv/infra")
        )
        tick(target)  # Run one reconciliation cycle
        ```
    """

    model_config = {"frozen": True, "arbitrary_types_allowed": True}

    name: str
    backend: BackEnd
    repo: Path
    notify: Notifier = Field(default=default_notifier)


@contextmanager
def backend_lock(name: str) -> Iterator[bool]:
    """Non-blocking file lock to prevent concurrent reconciliation of the same target.

    Uses fcntl.flock for advisory file locking. The lock is automatically released
    if the process dies, preventing stale locks. Yields True if lock acquired,
    False if another process is already reconciling this target.

    This is critical for safety: if two reconciliation processes run concurrently
    on the same backend, they can corrupt state files (Terraform, Pulumi) or cause
    race conditions (docker-compose, ansible).

    Args:
        name: Target name (becomes lock filename)

    Yields:
        True if lock was acquired, False if already held by another process

    Example:
        ```python
        with backend_lock("prod-infra") as acquired:
            if not acquired:
                logger.info("Skipping - reconciliation already in progress")
                return
            # Safe to proceed with reconciliation
        ```
    """
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOCK_DIR / f"{name}.lock"
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield True
    except BlockingIOError:
        yield False
    finally:
        fd.close()


def sync_git(repo: Path) -> None:
    """Sync local git repository to match remote origin/main.

    Fetches latest changes and hard-resets to origin/main, discarding any local
    modifications. This ensures the reconciler always works with the canonical
    desired state from the remote repository.

    WARNING: This is a destructive operation. Don't run this on repositories with
    uncommitted local changes you want to keep.

    Args:
        repo: Path to git repository

    Raises:
        subprocess.CalledProcessError: If git commands fail
    """
    subprocess.run(["git", "-C", str(repo), "fetch", "--quiet"], check=True)
    subprocess.run(["git", "-C", str(repo), "reset", "--hard", "origin/main"], check=True)


def current_sha(repo: Path) -> str:
    """Get the current git commit SHA for a repository.

    Returns the full 40-character SHA-1 hash of HEAD. This is used for provenance
    tracking - recording which git commit was successfully applied.

    Args:
        repo: Path to git repository

    Returns:
        Full git commit SHA (40 hex characters)

    Raises:
        subprocess.CalledProcessError: If git command fails
    """
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _state_file(target_name: str) -> Path:
    """Get the state file path for a target, creating parent directory if needed.

    State files store the last successfully applied git SHA plus the result and
    message from that reconciliation. This provides provenance tracking and helps
    avoid re-applying the same commit.

    Args:
        target_name: Unique target identifier

    Returns:
        Path to state file (may not exist yet)
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / f"{target_name}.json"


def record_last_sha(target_name: str, sha: str, status: Status) -> None:
    """Record the git SHA and status of a successful reconciliation.

    Writes a JSON state file with the git commit that was just applied, plus the
    result and message. This creates an audit trail and enables skip-on-no-change
    optimizations in future implementations.

    IMPORTANT: Only call this after successful reconciliation (NO_CHANGE or CHANGED).
    Never call on FAILED - recording an unverified SHA is worse than having stale data.

    Args:
        target_name: Unique target identifier
        sha: Git commit SHA that was successfully applied
        status: Status from the successful apply operation
    """
    _state_file(target_name).write_text(
        json.dumps({"sha": sha, "result": status.result.value, "message": status.message})
    )


def last_recorded_sha(target_name: str) -> str | None:
    """Retrieve the last successfully applied git SHA for a target.

    Reads the state file written by record_last_sha(). Returns None if this target
    has never been successfully reconciled.

    Future optimization: could use this to skip reconciliation when current_sha()
    matches last_recorded_sha() and the backend natively detects no-change.

    Args:
        target_name: Unique target identifier

    Returns:
        Git commit SHA from last successful reconciliation, or None if never reconciled
    """
    path = _state_file(target_name)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    sha: str | None = data.get("sha")
    return sha


def tick(target: ManagedTarget) -> None:
    """Execute one reconciliation cycle for a target.

    This is the core reconciliation loop. It:
    1. Attempts to acquire an exclusive lock (skips if locked)
    2. Syncs git to origin/main
    3. Calls backend.apply()
    4. Records the git SHA on success, or notifies on failure
    5. Logs the outcome

    Designed to be invoked once per execution (e.g., from cron or systemd timer),
    not looped internally. For continuous reconciliation, schedule this function
    to run periodically.

    Error Handling:
    - Lock acquisition failure: logs and returns (non-fatal, expected in concurrent scenarios)
    - Git sync failure: raises exception (fatal - can't reconcile without desired state)
    - Backend apply() exception: caught, converted to FAILED status, notification sent
    - Backend returns FAILED: triggers notification, does NOT record SHA

    Args:
        target: The ManagedTarget to reconcile

    Example:
        ```python
        # From a systemd timer or cron job:
        if __name__ == "__main__":
            target = ManagedTarget(...)
            tick(target)
        ```
    """
    with backend_lock(target.name) as acquired:
        if not acquired:
            logger.info("%s: previous tick still running, skipping", target.name)
            return

        sync_git(target.repo)

        try:
            status = target.backend.apply()
        except Exception as exc:  # noqa: BLE001 — deliberately broad.
            # A backend-internal failure (dropped SSH connection, corrupt
            # state, missing binary) must still surface as FAILED + a
            # notification, not an uncaught exception that silently skips
            # both record_last_sha and notify().
            logger.exception("%s: apply() raised", target.name)
            status = Status(result=ApplyResult.FAILED, message=f"{type(exc).__name__}: {exc}")

        if status.result == ApplyResult.FAILED:
            # Don't record a SHA against a failed/unverified apply — an
            # unverified "last applied" marker is worse than a stale one.
            target.notify(target.name, status)
        else:
            record_last_sha(target.name, current_sha(target.repo), status)

        logger.info("%s: %s — %s", target.name, status.result.value, status.message[:200])


def run(targets: list[ManagedTarget]) -> None:
    """Reconcile multiple targets sequentially in a single invocation.

    Convenience wrapper around tick() for managing multiple targets. Each target
    still acquires its own lock and can independently skip if locked, but they
    execute sequentially (not concurrently) within this call.

    For true parallel reconciliation across targets, schedule each target with its
    own systemd timer or cron job calling tick() directly.

    Args:
        targets: List of ManagedTarget instances to reconcile

    Example:
        ```python
        targets = [
            ManagedTarget(name="prod-infra", backend=terraform_backend, repo=Path("/srv/infra")),
            ManagedTarget(name="staging-infra", backend=staging_backend, repo=Path("/srv/staging")),
        ]
        run(targets)  # Reconciles both sequentially
        ```
    """
    for target in targets:
        tick(target)
