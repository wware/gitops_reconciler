# Staging -> Production Promotion Workflow

## Overview

The reconciler's provenance tracking (`record_last_sha` / `last_recorded_sha`
in `gitops_reconciler/wrapper.py`) naturally supports progressive delivery,
with no new abstractions required:

1. **Staging** watches a git repo and auto-deploys on every commit (tracking
   `:latest`, or whatever tag your CI pushes on merge)
2. **Production** watches the *same* repo but is pinned to a specific,
   already-validated image tag
3. A **promotion script** reads staging's last successful SHA and rewrites
   prod's pin to that SHA

Staging and prod reconcilers never talk to each other directly — git is the
only shared state, and each side has its own `ManagedTarget` with its own
lock and state file.

## Example: Docker Compose deployment

**Targets** (defined in `gitops_reconciler/example.py`):

- `demo-app-staging`: applies `example-app/docker-compose.staging.yml`
  (`gitops-demo-app:latest`)
- `demo-app-prod`: applies `example-app/docker-compose.prod.yml` (pinned to
  a specific tag, e.g. `gitops-demo-app:abc123f`)

Both targets share one repo (`example-app/`) but reference different
compose files — that's the entire trick. Nothing about `ManagedTarget` or
`tick()` needed to change to support this.

**Systemd timers** (production deployment, not the laptop demo):

```bash
# Staging ticks frequently — fast feedback on every commit
systemctl enable --now gitops-demo-app-staging.timer

# Production ticks less often — the pin only changes on promotion
systemctl enable --now gitops-demo-app-prod.timer
```

**Promotion**:

```bash
# One-time setup: copy the template and adjust it for your layout
cp promote.example.py promote.py

# Check what would be promoted
./promote.py --dry-run

# Promote staging's last successful SHA to prod
./promote.py

# Commit the updated compose file
git add example-app/docker-compose.prod.yml
git commit -m "Promote demo-app to staging SHA abc123f"
git push

# Next prod tick applies the promoted version
```

`promote.py` is gitignored (see `.gitignore`) because it hardcodes
deployment-specific paths and target names — `promote.example.py` is the
tracked template.

## Why this works

- **Staging's state file** (`/var/lib/gitops-agent/demo-app-staging.json` in
  production, or `/tmp/gitops-agent/state/demo-app-staging.json` in the
  laptop demo — see `DEMO.md`) contains the SHA that was last successfully
  applied to staging, written by `record_last_sha()`.
- **The promotion script** reads that SHA via `last_recorded_sha()` and
  rewrites prod's compose file to reference it as an image tag.
- **Production's reconciler** sees the compose file changed in git and
  applies it on its next tick — the same `ComposeBackend.apply()` path it
  always uses, no special-casing for "this is a promotion."
- **No API coupling**: staging and prod never call into each other. If
  staging's reconciler process is out of scope for prod's, no port needs to
  be opened.

## Extending to other backends

The pattern only depends on `record_last_sha` / `last_recorded_sha` and a
per-target config value the promotion script can rewrite. It applies
unchanged to any backend:

**Terraform**:
- Staging: `var_file=staging.tfvars` (`image_tag = "latest"`)
- Prod: `var_file=prod.tfvars` (`image_tag = "v1.2.3"`)
- Promotion: update `image_tag` in `prod.tfvars` to staging's SHA

**Pulumi**:
- Staging: `stack="dev"` (stack config `imageTag: latest`)
- Prod: `stack="prod"` (stack config `imageTag: v1.2.3`)
- Promotion: `pulumi config set --stack prod imageTag <staging-sha>`

Staging auto-applies, prod is pinned, promotion updates the pin — that's
the whole pattern regardless of backend.

## Non-goals

- **Automated promotion.** `promote.py` is manual by design. A policy layer
  ("promote automatically if staging has been healthy for 2 hours") can
  wrap this primitive, but doesn't belong in the core reconciler.
- **Rollback tooling.** `git revert` + the next tick already handles this —
  no special-case code needed.
- **Multi-stage pipelines** (dev -> staging -> prod). More of the same
  pattern, chained; not shown here to keep the example to two stages.

## Possible future enhancements

None of these are needed for the core pattern to work, and none are planned
— listed here only so the idea isn't lost if someone wants to extend this
later:

- Prometheus metrics (promotion lag, deployment frequency)
- Slack/webhook notification on promotion
- Health-check gating ("only promote if staging passed its tests")
- Automated rollback on prod health degradation
- Multi-region promotion (e.g. promote to us-east-1, wait, then eu-west-1)
