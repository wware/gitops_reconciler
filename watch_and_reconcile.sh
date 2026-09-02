#!/bin/bash
#
# GitOps Reconciler Watch Loop
#
# Continuously runs the reconciler at regular intervals to detect and apply
# changes from git commits. Designed for local development/demo purposes.
#
# Usage:
#   ./watch_and_reconcile.sh [INTERVAL]
#
# Arguments:
#   INTERVAL - Seconds between reconciliation attempts (default: 10)
#
# Example:
#   ./watch_and_reconcile.sh      # Check every 10 seconds
#   ./watch_and_reconcile.sh 5    # Check every 5 seconds
#   ./watch_and_reconcile.sh 60   # Check every 60 seconds
#

set -e

INTERVAL=${1:-10}  # Default 10 seconds, override with first argument

echo "╔════════════════════════════════════════════════════════════╗"
echo "║          GitOps Reconciler - Watch Mode                   ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "Checking every ${INTERVAL} seconds"
echo "Press Ctrl+C to stop"
echo ""

# Trap Ctrl+C for clean shutdown
trap 'echo ""; echo "Reconciler stopped."; exit 0' INT

while true; do
    echo "────────────────────────────────────────────────────────────"
    echo "🔄 [$(date '+%Y-%m-%d %H:%M:%S')] Running reconciliation..."
    echo ""

    # Run the reconciler
    uv run python reconcile_example.py

    echo ""
    echo "💤 Sleeping for ${INTERVAL}s..."
    sleep "$INTERVAL"
    echo ""
done
