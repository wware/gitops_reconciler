#!/usr/bin/env python3
"""Reconciler script for example-app demo.

This script manages the example-app Docker Compose stack using the GitOps
reconciler's ComposeBackend. It's designed for laptop/local development and
makes several prototype-specific choices:

- Uses /tmp for state and lock directories (no sudo required)
- Skips git sync (works with current working tree state)
- Runs on 'next' branch (not origin/main)
- Uses local Docker (no SSH)

For production deployment, you would:
- Use /var/lib and /var/run for state/locks
- Enable git sync to pull from remote
- Use systemd timers instead of bash loop
- Consider SSH for remote Docker hosts
"""

import logging
from pathlib import Path

from gitops_reconciler import wrapper
from gitops_reconciler.backends import build_backend
from gitops_reconciler.models import ApplyResult, ComposeConfig
from gitops_reconciler.wrapper import ManagedTarget, backend_lock, current_sha, record_last_sha

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Override state/lock directories for laptop demo (no sudo required)
wrapper.STATE_DIR = Path("/tmp/gitops-agent/state")
wrapper.LOCK_DIR = Path("/tmp/gitops-agent/locks")


def tick_without_git_sync(target: ManagedTarget) -> None:
    """Execute one reconciliation cycle WITHOUT git sync.

    This is a prototype-specific variant of the standard tick() function.
    It skips the sync_git() call because:
    - We're on 'next' branch, not 'main'
    - We want to test with local uncommitted changes
    - We don't want to pull from remote on every tick

    For production use, you would use the standard tick() function from
    gitops_reconciler.wrapper which includes git sync.

    Args:
        target: The ManagedTarget to reconcile
    """
    with backend_lock(target.name) as acquired:
        if not acquired:
            logger.info("%s: previous tick still running, skipping", target.name)
            return

        # PROTOTYPE: Skip git sync, use current working tree state
        # In production, this would be: sync_git(target.repo)

        try:
            status = target.backend.apply()
        except Exception as exc:
            logger.error("%s: apply() raised exception: %s", target.name, exc)
            target.notify(target.name, wrapper.Status(result=ApplyResult.FAILED, message=str(exc)))
            return

        # Log the result
        if status.result == ApplyResult.NO_CHANGE:
            logger.info("%s: NO_CHANGE - stack already up to date", target.name)
        elif status.result == ApplyResult.CHANGED:
            logger.info("%s: CHANGED - stack updated successfully", target.name)
        elif status.result == ApplyResult.FAILED:
            logger.error("%s: FAILED - %s", target.name, status.message)
            target.notify(target.name, status)
            return

        # Record provenance on success
        sha = current_sha(target.repo)
        record_last_sha(target.name, sha, status)
        logger.debug("%s: recorded SHA %s", target.name, sha[:8])


def main() -> None:
    """Run one reconciliation cycle for the example-app."""
    # Get absolute paths
    repo_root = Path(__file__).parent.absolute()
    compose_file = repo_root / "example-app" / "docker-compose.yml"

    # Validate compose file exists
    if not compose_file.exists():
        logger.error("Compose file not found: %s", compose_file)
        return

    # Configure ComposeBackend
    config = ComposeConfig(
        compose_file=compose_file,
        host="local",  # Run locally, no SSH
        project_name="gitops-demo",  # Avoid conflicts with other compose projects
    )

    # Build backend and create managed target
    backend = build_backend(config)
    target = ManagedTarget(
        name="example-app",
        backend=backend,
        repo=repo_root,
    )

    # Run one reconciliation tick
    logger.info("=" * 60)
    logger.info("Starting reconciliation for %s", target.name)
    logger.info("Compose file: %s", compose_file)
    logger.info("=" * 60)

    tick_without_git_sync(target)

    logger.info("=" * 60)
    logger.info("Reconciliation complete")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
