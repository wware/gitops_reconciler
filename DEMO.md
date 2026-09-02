# GitOps Reconciler Demo

This demo shows the GitOps reconciler managing a Docker Compose stack on your laptop. The reconciler automatically detects changes to the `example-app/` directory and updates the running containers.

## Quick Start

```bash
# 1. Start the reconciler watch loop (checks every 10 seconds)
./watch_and_reconcile.sh

# 2. In another terminal, make a change to the compose file
sed -i 's/9001:8080/9002:8080/' example-app/docker-compose.yml
git add example-app/docker-compose.yml
git commit -m "Change port to 9002"

# 3. Watch the first terminal - within 10 seconds you'll see:
#    - Reconciler detects the change
#    - Docker Compose updates the stack
#    - App is now running on port 9002

# 4. Verify the change
curl http://localhost:9002/
```

## What's Happening Under the Hood

The demo implements a complete GitOps reconciliation loop:

### Components

1. **Example App** (`example-app/`)
   - FastAPI CRUD application with SQLite database
   - Serves a web UI at `/` and REST API at `/items`
   - Packaged as a Docker container

2. **Reconciler Script** (`reconcile_example.py`)
   - Creates a `ManagedTarget` with `ComposeBackend`
   - Runs one reconciliation cycle per invocation
   - Uses hash-based idempotency to detect changes

3. **Watch Loop** (`watch_and_reconcile.sh`)
   - Invokes the reconciler every 10 seconds (configurable)
   - Simulates a systemd timer or cron job
   - Provides visibility into reconciliation cycles

### Reconciliation Flow

Each time the reconciler runs:

```
1. Acquire lock (/tmp/gitops-agent/locks/example-app.lock)
   └─> Skip if another reconciliation is in progress

2. Compute hash of docker-compose.yml
   └─> SHA256 digest of file contents

3. Compare to last applied hash
   ├─> Match? Return NO_CHANGE, skip docker compose
   └─> Different? Continue to step 4

4. Run docker compose up -d
   └─> Updates containers to match desired state

5. Store new hash (/tmp/gitops-agent/state/.last_applied_hash)
   └─> Next run will compare against this

6. Record git SHA and status
   └─> Provenance tracking for audit trail
```

### Why Hash-Based Idempotency?

Unlike Terraform or Pulumi (which have native diff capabilities), Docker Compose doesn't provide a "preview changes" mode. The `ComposeBackend` works around this by:

- Computing a hash of the compose file before applying
- Storing the hash after successful application
- Skipping `docker compose up` when hash hasn't changed

This prevents unnecessary container restarts and provides fast no-op checks when nothing has changed.

## Step-by-Step Demo

### Initial Setup

```bash
# Ensure dependencies are installed
uv sync --all-extras

# Run the reconciler once to start the stack
uv run python reconcile_example.py
```

Expected output:
```
============================================================
Starting reconciliation for example-app
Compose file: .../example-app/docker-compose.yml
============================================================
example-app: CHANGED - stack updated successfully
============================================================
```

Verify containers are running:
```bash
docker compose -f example-app/docker-compose.yml -p gitops-demo ps
```

Test the app:
```bash
# Web UI
curl http://localhost:9001/

# API
curl http://localhost:9001/items
```

### Test Idempotency

Run the reconciler again without changing anything:

```bash
uv run python reconcile_example.py
```

Expected output:
```
example-app: NO_CHANGE - stack already up to date
```

Notice how fast this is - no docker compose command was executed because the hash matched.

### Test Change Detection

Start the watch loop in one terminal:

```bash
./watch_and_reconcile.sh
```

In another terminal, make a change:

```bash
# Change the port mapping
sed -i 's/9001:8080/9002:8080/' example-app/docker-compose.yml

# Commit it
git add example-app/docker-compose.yml
git commit -m "Change demo app port to 9002"
```

Watch the first terminal - within 10 seconds you should see:
```
🔄 [2026-09-02 10:45:30] Running reconciliation...
...
example-app: CHANGED - stack updated successfully
```

Verify the change was applied:
```bash
# Old port should not respond
curl http://localhost:9001/  # Connection refused

# New port should work
curl http://localhost:9002/  # Returns HTML
```

### Test Reversion

Revert the change:

```bash
sed -i 's/9002:8080/9001:8080/' example-app/docker-compose.yml
git add example-app/docker-compose.yml
git commit -m "Revert port back to 9001"
```

Watch the reconciler detect and apply the reversion automatically.

### Cleanup

Stop the watch loop:
```bash
# In the terminal running watch_and_reconcile.sh
Ctrl+C
```

Stop and remove containers:
```bash
docker compose -f example-app/docker-compose.yml -p gitops-demo down
```

Clean up state files:
```bash
rm -rf /tmp/gitops-agent
```

## Observing Reconciliation

### View State Files

```bash
# Lock file (exists only while reconciliation is running)
ls -la /tmp/gitops-agent/locks/

# State file (persists between runs)
cat /tmp/gitops-agent/state/example-app.json
```

The state file contains:
```json
{
  "sha": "bd6fe3f...",
  "result": "changed",
  "message": ""
}
```

### View Last Applied Hash

```bash
# Hash stored by ComposeBackend
cat example-app/.last_applied_hash
```

### Watch Docker Logs

In a separate terminal:
```bash
docker compose -f example-app/docker-compose.yml -p gitops-demo logs -f
```

You'll see the uvicorn server restart when the reconciler applies changes.

## Customizing the Demo

### Change Poll Interval

```bash
# Check every 5 seconds (faster feedback)
./watch_and_reconcile.sh 5

# Check every 60 seconds (less noisy)
./watch_and_reconcile.sh 60
```

### Run Reconciler Manually

Instead of the watch loop, run reconciliation on demand:

```bash
uv run python reconcile_example.py
```

### Modify the App

The example app is a simple CRUD application. Try modifying it:

```python
# example-app/app.py
# Add a new endpoint, change the UI, etc.
```

After committing the change, the reconciler will:
1. Detect the docker-compose.yml hash is the same → NO_CHANGE
2. **Not rebuild** the container automatically

To force a rebuild, you need to trigger a compose file change. You could:
- Bump a version in an environment variable
- Add a comment
- Touch the file to change its modification time (but this won't change the hash)

For automatic rebuilds on code changes, you'd need a more sophisticated backend that monitors the build context, not just the compose file.

## Troubleshooting

### Containers not starting

```bash
# Check Docker is running
docker version

# Check for port conflicts
lsof -i :9001

# View container logs
docker compose -f example-app/docker-compose.yml -p gitops-demo logs
```

### "Permission denied" on /tmp

The demo uses `/tmp/gitops-agent/` for state and locks. This should be writable by all users, but if you encounter issues:

```bash
mkdir -p /tmp/gitops-agent/{state,locks}
chmod 777 /tmp/gitops-agent/{state,locks}
```

### Reconciler reports CHANGED every time

The hash-based idempotency should prevent this. If you see CHANGED on every run:

```bash
# Check if hash file exists
ls -la example-app/.last_applied_hash

# Check if hash is being written
cat example-app/.last_applied_hash

# Verify compose file isn't being modified
git diff example-app/docker-compose.yml
```

### Git state confusion

The demo skips git sync to work with local changes. If you've made commits and the reconciler isn't detecting them:

1. The reconciler works with the **working tree**, not commits
2. Changes must be **written to disk** (not just committed)
3. The hash is computed from the **file contents**, not the git diff

To sync with a commit:
```bash
git checkout <commit-sha>
```

## Differences from Production Use

This demo makes several simplifications for laptop testing:

| Demo | Production | Why Different? |
|------|-----------|----------------|
| `/tmp` for state/locks | `/var/lib` and `/var/run` | `/var` requires root; `/tmp` is universally writable and auto-cleaned on reboot |
| Skips git sync | Pulls from remote on every tick | Demo works with local changes; production needs canonical state from remote |
| Works with uncommitted changes | Requires clean git state | Demo allows rapid iteration; production should only apply reviewed commits |
| Bash loop with sleep | Systemd timer or cron | OS scheduler provides restart-on-failure, logging, resource limits for free |
| Local Docker | Often remote via SSH | Demo runs on laptop; production often manages remote hosts |
| 10-second polling | 60-300 second intervals | Fast feedback for demo; production uses longer intervals to reduce load |

### Why Skip Git Sync?

The standard `sync_git()` function in `wrapper.py` assumes:
- The repo has a clean working tree (no uncommitted changes)
- The remote branch is `origin/main`
- Pulling from remote is always desired

For this laptop demo:
- We're on the `next` branch (not `main`)
- We want to test with uncommitted local edits
- We don't want to overwrite local changes with remote pulls

**Solution:** `reconcile_example.py` uses a custom `tick_without_git_sync()` function that skips the git operations and works directly with the current working tree. For production, you'd use the standard `tick()` function with proper git sync enabled.

### Why These Choices Matter

These demo simplifications make it easy to get started, but understanding the trade-offs helps when moving to production:

- **State in /tmp**: Convenient but ephemeral - reboot clears all state
- **No git sync**: Fast iteration but no guarantee you're testing what will actually deploy
- **Short polling**: Responsive but can miss changes if reconciliation takes longer than interval
- **Bash loop**: Simple but lacks monitoring, logging, and error recovery

For production deployment, see `gitops_reconciler/example.py` which shows:
- Systemd timer configuration
- Proper git sync workflow
- Multiple backends running independently
- Production-grade error handling

## Next Steps

- Read the [architecture documentation](README.md) to understand the backend abstraction
- Explore other backends: `TerraformBackend`, `PulumiBackend`, `PiBackend`
- Implement a custom backend for your infrastructure
- Set up systemd timers for production deployment
- Add monitoring/observability (Prometheus, structured logging)
- Integrate notification backends (Slack, PagerDuty, email)
