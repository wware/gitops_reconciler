#!/usr/bin/env python3
"""Promotion bot: copy staging's last successful SHA to prod's compose file pin.

This is a template. Copy it to promote.py (gitignored) and adjust
STAGING_TARGET / PROD_COMPOSE for your own deployment layout — the demo
values below match gitops_reconciler/example.py's demo-app-staging and
demo-app-prod targets.

Usage:
    ./promote.py                # promote staging -> prod
    ./promote.py --dry-run      # show what would be promoted

See PROMOTION.md for the full workflow this script is part of.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from gitops_reconciler import last_recorded_sha

STAGING_TARGET = "demo-app-staging"
PROD_COMPOSE = Path(__file__).resolve().parent / "example-app" / "docker-compose.prod.yml"


def get_staging_sha(staging_target: str) -> str | None:
    """Retrieve the git SHA that staging last successfully applied."""
    return last_recorded_sha(staging_target)


def get_current_prod_pin(compose_file: Path) -> str | None:
    """Extract the current image tag from prod's compose file."""
    content = compose_file.read_text()
    match = re.search(r"image:\s*\S+:(\S+)", content)
    return match.group(1) if match else None


def update_prod_pin(compose_file: Path, new_sha: str, dry_run: bool = False) -> None:
    """Update prod compose file to pin to new_sha as the image tag."""
    content = compose_file.read_text()
    updated = re.sub(
        r"(image:\s*\S+:)\S+",
        rf"\g<1>{new_sha[:7]}",  # Use short SHA as tag
        content,
    )

    if dry_run:
        print(f"[DRY RUN] Would update {compose_file}:")
        print(updated)
    else:
        compose_file.write_text(updated)
        print(f"Updated {compose_file} to SHA {new_sha[:7]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote staging to production")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    args = parser.parse_args()

    staging_sha = get_staging_sha(STAGING_TARGET)
    if not staging_sha:
        print(f"ERROR: No successful apply recorded for {STAGING_TARGET}")
        return

    current_pin = get_current_prod_pin(PROD_COMPOSE)
    print(f"Staging last applied: {staging_sha[:7]}")
    print(f"Production current pin: {current_pin}")

    if staging_sha[:7] == current_pin:
        print("Production is already at staging's SHA, no promotion needed")
        return

    update_prod_pin(PROD_COMPOSE, staging_sha, dry_run=args.dry_run)

    if not args.dry_run:
        print("\nPromotion complete. Next reconciliation tick will apply the change.")
        print(f"Commit and push the updated compose file to promote {staging_sha[:7]} to prod")


if __name__ == "__main__":
    main()
