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
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    args = parser.parse_args()
    tick(TARGETS[args.target])


if __name__ == "__main__":
    main()
