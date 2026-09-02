"""Example wiring for three independent targets, each meant to be driven
by its own systemd timer:

    # /etc/systemd/system/gitops-pi-livingroom.timer
    [Timer]
    OnCalendar=*:0/5
    [Install]
    WantedBy=timers.target

    # /etc/systemd/system/gitops-pi-livingroom.service
    [Service]
    Type=oneshot
    ExecStart=/usr/bin/python3 -m gitops_reconciler.example --target pi-livingroom

Swap the OnCalendar value and --target per unit; one pair of timer+service
files per managed target keeps them scheduling and failing independently.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .backends import build_backend
from .models import ComposeConfig, PiConfig
from .wrapper import ManagedTarget, tick

logging.basicConfig(level=logging.INFO)

# Demo repo shared by the staging/prod pair below. Both targets watch the
# same repo but apply different compose files — see PROMOTION.md for how
# this composes into a promotion workflow.
DEMO_REPO = Path(__file__).resolve().parent.parent / "example-app"
STAGING_COMPOSE = DEMO_REPO / "docker-compose.staging.yml"
PROD_COMPOSE = DEMO_REPO / "docker-compose.prod.yml"

# Target definitions: one entry per managed infrastructure component.
# Each target combines a backend type, git repository, and unique name.
# The name serves as both the CLI argument and the lock/state file identifier.
TARGETS: dict[str, ManagedTarget] = {
    "pi-livingroom": ManagedTarget(
        name="pi-livingroom",
        backend=build_backend(
            PiConfig(host="pi-livingroom.lan", repo_path=Path("/home/pi/gitops-config"))
        ),
        repo=Path("/home/pi/gitops-config"),
    ),
    "demo-app-staging": ManagedTarget(
        name="demo-app-staging",
        backend=build_backend(
            ComposeConfig(
                compose_file=STAGING_COMPOSE,
                host="local",
                project_name="gitops-demo-staging",
            )
        ),
        repo=DEMO_REPO,
    ),
    "demo-app-prod": ManagedTarget(
        name="demo-app-prod",
        backend=build_backend(
            ComposeConfig(
                compose_file=PROD_COMPOSE,
                host="local",
                project_name="gitops-demo-prod",
            )
        ),
        repo=DEMO_REPO,
    ),
}


def main() -> None:
    """CLI entry point for single-target reconciliation.

    Accepts a --target argument to select which target to reconcile from the
    TARGETS registry. Designed to be invoked by systemd timers or cron jobs
    with specific target names.

    Usage:
        python -m gitops_reconciler.example --target pi-livingroom
        python -m gitops_reconciler.example --target demo-app-staging
        python -m gitops_reconciler.example --target demo-app-prod

    Each invocation runs exactly one reconciliation cycle (tick) for the selected
    target. For continuous reconciliation, schedule this command to run periodically.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    args = parser.parse_args()
    tick(TARGETS[args.target])


if __name__ == "__main__":
    main()
