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
from .models import ComposeConfig, PiConfig, TerraformConfig
from .wrapper import ManagedTarget, tick

logging.basicConfig(level=logging.INFO)

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
    "staging-compose": ManagedTarget(
        name="staging-compose",
        backend=build_backend(
            ComposeConfig(
                compose_file=Path("/srv/staging/docker-compose.yml"),
                host="staging.internal",
            )
        ),
        repo=Path("/srv/staging"),
    ),
    "prod-network": ManagedTarget(
        name="prod-network",
        backend=build_backend(TerraformConfig(workdir=Path("/srv/infra/network"))),
        repo=Path("/srv/infra"),
    ),
}


def main() -> None:
    """CLI entry point for single-target reconciliation.

    Accepts a --target argument to select which target to reconcile from the
    TARGETS registry. Designed to be invoked by systemd timers or cron jobs
    with specific target names.

    Usage:
        python -m gitops_reconciler.example --target pi-livingroom
        python -m gitops_reconciler.example --target staging-compose
        python -m gitops_reconciler.example --target prod-network

    Each invocation runs exactly one reconciliation cycle (tick) for the selected
    target. For continuous reconciliation, schedule this command to run periodically.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    args = parser.parse_args()
    tick(TARGETS[args.target])


if __name__ == "__main__":
    main()
