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
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from .backends import BackEnd
from .models import ApplyResult, Status

logger = logging.getLogger("gitops_reconciler.wrapper")

STATE_DIR = Path("/var/lib/gitops-agent")
LOCK_DIR = Path("/var/run/gitops-agent")

Notifier = Callable[[str, Status], None]


def default_notifier(target_name: str, status: Status) -> None:
    logger.error("RECONCILE FAILED for %s: %s", target_name, status.message)


@dataclass(frozen=True)
class ManagedTarget:
    """One (name, backend, repo) tuple with its own lock, schedule, and
    provenance file.

    `name` must already be globally unique across every target you manage
    — it doubles as the lock key and the state-file key. If you're running
    the same backend kind against multiple hosts (e.g. several PiBackend
    instances), compose the uniqueness yourself at the call site, e.g.
    `name=f"pi-{hostname}"`. `backend_lock` does no composition of its own.
    """

    name: str
    backend: BackEnd
    repo: Path
    notify: Notifier = field(default=default_notifier)


@contextmanager
def backend_lock(name: str) -> Iterator[bool]:
    """Non-blocking flock scoped to `name`. Self-releases if the process
    dies, so there's no stale-lock file to clean up. Yields True if the
    lock was acquired, False if another tick for this same `name` is
    already running (in which case the caller should skip, not wait).
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
    subprocess.run(["git", "-C", str(repo), "fetch", "--quiet"], check=True)
    subprocess.run(["git", "-C", str(repo), "reset", "--hard", "origin/main"], check=True)


def current_sha(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _state_file(target_name: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / f"{target_name}.json"


def record_last_sha(target_name: str, sha: str, status: Status) -> None:
    _state_file(target_name).write_text(
        json.dumps({"sha": sha, "result": status.result.value, "message": status.message})
    )


def last_recorded_sha(target_name: str) -> str | None:
    path = _state_file(target_name)
    if not path.exists():
        return None
    return json.loads(path.read_text()).get("sha")


def tick(target: ManagedTarget) -> None:
    """One reconciliation attempt for one target. Meant to be invoked once
    per process — by a systemd timer or cron entry — not looped internally.
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
    """Convenience for reconciling several targets from one invocation.
    Each target still takes its own lock and is independently skippable,
    but they run sequentially within this call. For true concurrency
    across targets, give each its own systemd timer calling `tick()`
    directly instead of routing everything through `run()`.
    """
    for target in targets:
        tick(target)
