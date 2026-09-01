# A Minimal Multi-Backend GitOps Reconciler

## Quick Start

```bash
# Install dependencies with uv
uv sync --all-extras

# Run tests
uv run pytest

# Run linting and type checking
bash lint.sh
```

## Motivation

GitOps — git as source of truth, an agent that reconciles actual state to
desired state — is not inherently a Kubernetes concept. Kubernetes just
happens to supply a live, declarative, self-diffing control plane for free,
which is why ArgoCD/Flux only had to build the git-polling layer on top of
it. Outside Kubernetes, every target (a Terraform-managed cloud stack, a
Pulumi program, a CloudFormation deployment, a Docker Compose host, a
Raspberry Pi on a LAN) needs the reconciliation loop built explicitly.

This document sketches a small, backend-agnostic reconciler for that
general case: one wrapper process, driven by a git repo, capable of
managing several unrelated targets on independent schedules, with the
apply mechanism decided per-target and the pull/schedule/locking logic
factored out into a single shared piece of code.

## Credentials and where the wrapper runs

For the cloud backends (Terraform, Pulumi, CloudFormation) the wrapper
needs credentials broad enough to create, modify, and destroy managed
resources — which makes it a meaningful attack surface. The reconciler
should run on a separate control machine, not on the target it manages:
if a target is ever compromised, it should not thereby gain access to the
credentials that can reprovision infrastructure. Prefer short-lived
STS/instance-role credentials over static long-lived keys wherever the
provider supports it.

This concern is backend-dependent rather than universal. It matters a
lot for the cloud backends. It's largely moot for the `PiBackend` case —
there's no meaningful privilege boundary between "the wrapper" and "the
Pi it manages" worth protecting, so running the reconciler loop on the
Pi itself (or on a machine on the same LAN) is fine.

## The `BackEnd` protocol

```python
from typing import Protocol, Dict, Any
from enum import Enum
from pydantic import BaseModel


class ApplyResult(str, Enum):
    NO_CHANGE = "no_change"
    CHANGED = "changed"
    FAILED = "failed"


class Status(BaseModel, frozen=True):
    result: ApplyResult
    message: str = ""


class BackEnd(Protocol):
    def apply(self) -> Status: ...
    def destroy(self) -> Status: ...
    def get_outputs(self) -> Dict[str, Any]: ...
```

Three methods, all argument-free. Anything a specific backend needs
(working directory, stack name, compose file path, SSH target, var files)
is bound at construction time — `TerraformBackend(workdir=...)`,
`PulumiBackend(stack=...)`, `ComposeBackend(compose_file=..., host=...)` —
so every concrete backend satisfies the same Protocol regardless of how
different its internals are.

Per-backend config is deliberately not unified into one shared struct.
Pulumi's project/stack/backend settings, Terraform's backend block, and
CloudFormation's account/region/stack-name don't overlap enough to be
worth forcing into a lowest-common-denominator shape with unused fields
per backend. A discriminated union of per-backend frozen config models —
one shape per backend, each fully its own — is the intended approach;
the Protocol only constrains the three methods, not how a backend gets
configured.

## Design reasoning

**Why no `plan()` / dry-run method.** The obvious first instinct is to
split "check for drift" from "apply the fix," mirroring `terraform plan`
vs `terraform apply`, or Pulumi's `preview`/`--refresh` vs `up`. This was
seriously considered and rejected. All three real backends (Terraform,
Pulumi, CloudFormation) are idempotent by construction: a full state
diff against reality is *inherent* to what `apply` does before it decides
whether to mutate anything. A no-op tick costs the same diff work whether
you call `plan()` then skip `apply()`, or just call `apply()` and let it
discover there's nothing to do. Splitting the method into two doesn't
save work — it just adds a second call site and forces the wrapper to
know something it doesn't need to know. The backend already knows whether
there's drift; if there isn't, it does nothing and returns fast. That's
the whole idea idempotency is supposed to buy you.

**Why `apply()` also absorbs whatever "refresh" means for a given
backend.** Pulumi and Terraform each maintain an external state artifact
(a local file, or a managed backend like S3 or Pulumi Cloud) that can go
stale relative to the real world — that's specifically what
`refresh`/`--refresh` exists to correct. CloudFormation has no such
artifact: AWS itself is the live state, queried fresh on every operation,
so "refresh" has nothing to reconcile against. Exposing a `refresh()`
method on the shared Protocol would therefore be meaningful for two
backends and a permanent no-op for the third — a leaky abstraction.
Keeping refresh-or-not entirely inside each backend's `apply()`
implementation (Pulumi's adapter can call `refresh()` then `up()`
internally; Terraform's `apply` already does this by default;
CloudFormation's `apply()` just creates and executes a change set) means
the Protocol never asks a question one of its implementers structurally
can't answer.

**Why no dry-run / human-approval gate.** A fully autonomous, no-human-
in-the-loop reconciler is a deliberate choice, not an oversight. Staging
environments are where a backend's behavior gets vetted before it's
trusted to run unattended against anything that matters; logs after the
fact are enough for post-hoc review. If a specific backend ever does need
a review gate, that belongs on that backend's constructor
(`TerraformBackend(dry_run=True)`) rather than as a new required method
on the Protocol — most backends and most ticks don't need it, and the
interface shouldn't carry a capability that's only ever used by one
caller.

**Why pull-vs-push is a wrapper concern, not a backend concern.** Each
decision belongs to exactly one layer. When to run, how
often, and what triggers a run (cron tick, webhook, human invocation)
are questions the backend has no business answering — it doesn't know
and shouldn't need to. Conversely, how to detect and achieve idempotent
convergence is a question the wrapper has no business answering — that's
what `apply()` is for. Keeping that boundary strict is what keeps the
Protocol tiny: the backend never sees a trigger, and the wrapper never
touches a diff.

**Why git sync lives in the wrapper, not the backend.** Pulling the
repo, checking out the right commit, deciding whether anything changed
since last sync — none of that is backend-specific, so it happens once,
in the wrapper, before any backend method is called.

**Why locking is per-backend-instance, not global.** A single flock on
the whole wrapper process would mean one slow Terraform apply blocks an
unrelated Pi tick from running, even though the two share no state.
`fcntl.flock` is preferable to a hand-rolled PID file because it
self-releases if the process dies — no stale-lock cleanup logic needed.
Lock scope is `(backend_name, target)`, so independent targets tick
independently:

```python
import fcntl
from pathlib import Path
from contextlib import contextmanager


@contextmanager
def backend_lock(name: str):
    lock_path = Path(f"/var/run/gitops-agent/{name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield True
    except BlockingIOError:
        yield False
    finally:
        fd.close()
```

## The wrapper loop

The actual implementation uses a `ManagedTarget` pydantic model to encapsulate
the target configuration:

```python
from pydantic import BaseModel, Field


class ManagedTarget(BaseModel):
    """One (name, backend, repo) tuple with its own lock, schedule, and
    provenance file.
    """

    model_config = {"frozen": True, "arbitrary_types_allowed": True}

    name: str
    backend: BackEnd  # Any object implementing the BackEnd protocol
    repo: Path
    notify: Notifier = Field(default=default_notifier)


def tick(target: ManagedTarget) -> None:
    """One reconciliation attempt for one target."""
    with backend_lock(target.name) as acquired:
        if not acquired:
            return  # previous tick still running; skip
        sync_git(target.repo)

        try:
            status = target.backend.apply()
        except Exception as exc:
            status = Status(result=ApplyResult.FAILED, message=f"{type(exc).__name__}: {exc}")

        if status.result == ApplyResult.FAILED:
            target.notify(target.name, status)
        else:
            record_last_sha(target.name, current_sha(target.repo), status)
```

Two additions beyond the bare minimum:

- **Provenance.** Persisting the git SHA that was last successfully
  applied, per backend, gives an audit trail and — combined with the
  logged `Status` — lets you distinguish "no-op because nothing changed"
  from "failed and didn't get as far as applying anything."
- **Failure notification.** "No human in the loop" was a decision about
  *approval*, not about *visibility*. A pluggable notifier callable fired
  on `FAILED` (email, webhook, whatever) means failures surface promptly
  instead of waiting to be noticed in a log; the reconciler still never
  blocks or asks permission, it just tells you when it couldn't converge.

Each managed target gets its own `(name, backend instance, repo path)`
tuple and its own schedule. Prefer a **one-shot invocation** — the
process runs a single `tick()` and exits — driven by a systemd timer or
cron entry, over a long-lived process doing its own `while`/`sleep`
loop. A one-shot gets restart-on-failure, logging, and scheduling for
free from the OS-level supervisor instead of that logic having to be
built and maintained inside the reconciler itself. Nothing about the
loop changes based on which backend is plugged in.

## Envisioned backends and use cases

| Backend | `apply()` does | `get_outputs()` returns | Notes |
|---|---|---|---|
| `TerraformBackend` / `OpenTofuBackend` | `terraform apply -auto-approve` (implicit refresh + diff) | `terraform output -json` | Cheap idempotency check is native. |
| `PulumiBackend` | `pulumi up --refresh -y`, likely via the Automation API rather than shelling out to the CLI | `stack.outputs()` | `--refresh` is required or Pulumi won't notice out-of-band drift. Caveat: refresh overwrites Pulumi's state to match live reality, including manual out-of-band changes — it doesn't revert those, it only means fields the program explicitly manages get corrected back on the *next* `up`. |
| `CloudFormationBackend` | create/update the stack via a change set applied automatically | stack outputs from the CFN API | Change sets give a natural diff step for free, same as Terraform. |
| `ComposeBackend` | `docker-compose up -d` on a target host | container status via `docker inspect` | No native dry-run diff — "no drift" has to be faked by hashing rendered config against last-applied hash. |
| `AnsibleBackend` | `ansible-playbook` run | whatever facts/registered vars the playbook exposes | Same faked-idempotency-check caveat as Compose unless playbooks are written carefully. |
| `PiBackend` (edge/LAN case) | `git pull` + `docker-compose up -d` or `systemctl restart` on a Raspberry Pi | service/container status | Good stress test of the abstraction — cheapest possible target, no cloud API involved. |

The Compose/Ansible/Pi cases are the interesting ones: unlike
Terraform/Pulumi/CloudFormation, they don't give you a free, engine-level
diff, so "idempotent no-op when nothing changed" has to be built by the
backend author (typically: hash the rendered config, compare to the hash
from the last successful apply, skip if unchanged). That's a genuine cost
of using a lighter-weight target, and it's useful signal about which
backends are "free" idempotency and which aren't.

## What this deliberately doesn't do

- No plan/preview/dry-run surface — see reasoning above.
- No human approval step — unattended by design; staging catches bad
  backend behavior before it's trusted against anything that matters.
- No persistent scheduling loop of its own — `tick()` is a one-shot,
  meant to be invoked by a systemd timer or cron entry so restart-on-
  failure and scheduling come from the OS rather than from code the
  reconciler has to maintain.
- No built-in rollback — `destroy()` exists for tearing a target down
  entirely, not for reverting to a previous good state. Rollback, if
  ever needed, is a property of the desired-state history in git (revert
  the commit, let the next tick reconcile to the reverted state) rather
  than a Protocol method.

## Implementation Status

### ✅ Completed

- **Core Protocol**: `BackEnd` protocol with `apply()`, `destroy()`, `get_outputs()`
- **Configuration Models**: Frozen Pydantic models for all 6 backend types
  - `TerraformConfig` / `PulumiConfig` / `CloudFormationConfig`
  - `ComposeConfig` / `AnsibleConfig` / `PiConfig`
- **Backend Implementations**: All 6 backends implemented
  - `TerraformBackend` - full implementation via subprocess
  - `PulumiBackend` - subprocess implementation (Automation API recommended for production)
  - `CloudFormationBackend` - stub (boto3 implementation recommended)
  - `ComposeBackend` - full implementation with hash-based idempotency
  - `AnsibleBackend` - full implementation
  - `PiBackend` - full implementation with repo-wide content hashing
- **Wrapper Logic**: Complete reconciliation loop
  - `ManagedTarget` pydantic model for configuration
  - Per-target file-based locking via `fcntl.flock`
  - Git sync with SHA provenance tracking
  - Exception handling and failure notification
  - `tick()` for single-target reconciliation
  - `run()` for multi-target sequential execution
- **Testing**: Comprehensive test suite (75 tests, 84% coverage)
  - Unit tests for all models with immutability verification
  - Backend tests with mocked subprocess calls
  - Wrapper tests for locking, git sync, and reconciliation logic
- **Code Quality**:
  - Strict mypy type checking (100% compliant)
  - Ruff linting and formatting
  - All models use immutable Pydantic models with `frozen=True`

### 🚧 Recommended Production Enhancements

- **PulumiBackend**: Replace subprocess calls with Pulumi Automation API
- **CloudFormationBackend**: Implement using boto3's `cloudformation` client
- **Observability**: Add structured logging, metrics export (Prometheus, etc.)
- **Notification backends**: Email, Slack, PagerDuty integrations
- **Multi-target concurrency**: Parallel execution across independent targets
- **Dry-run mode**: Optional per-backend flag for manual verification workflows

## Development

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager

### Setup

```bash
# Clone the repository
git clone <repo-url>
cd gitops_reconciler

# Install dependencies
uv sync --all-extras
```

### Running Tests

```bash
# Run all tests with coverage
uv run pytest

# Run specific test file
uv run pytest tests/test_backends.py -v

# Run tests matching a pattern
uv run pytest -k "test_terraform"
```

### Linting and Type Checking

The project uses `lint.sh` which runs:

```bash
# Check for linting issues
uv run ruff check .

# Auto-fix linting issues and format code
uv run ruff check --fix .
uv run ruff format .

# Type checking with mypy (strict mode)
uv run mypy --strict .

# Run all checks
bash lint.sh
```

### Project Structure

```
gitops_reconciler/
├── gitops_reconciler/
│   ├── __init__.py          # Public API exports
│   ├── models.py            # Pydantic config models and enums
│   ├── backends.py          # BackEnd protocol and implementations
│   ├── wrapper.py           # Reconciliation loop and locking
│   └── example.py           # Example usage with systemd timers
├── tests/
│   ├── test_models.py       # Model and config tests
│   ├── test_backends.py     # Backend implementation tests
│   └── test_wrapper.py      # Wrapper logic tests
├── pyproject.toml           # Project metadata and tool configuration
├── lint.sh                  # Linting and testing script
└── README.md                # This file
```

### Example Usage

See `gitops_reconciler/example.py` for a complete example showing:

- Three different backend types (Terraform, Compose, Pi)
- Independent scheduling via systemd timers
- Command-line interface for invoking specific targets

```python
from pathlib import Path
from gitops_reconciler import ManagedTarget, build_backend, tick
from gitops_reconciler.models import TerraformConfig

# Create a managed target
target = ManagedTarget(
    name="prod-network",
    backend=build_backend(TerraformConfig(workdir=Path("/srv/infra/network"))),
    repo=Path("/srv/infra"),
)

# Run one reconciliation tick
tick(target)
```

### Design Principles

- **Immutability**: All configuration models are frozen Pydantic models
- **Type Safety**: Strict mypy compliance throughout
- **Testability**: Comprehensive unit tests with mocked external dependencies
- **Simplicity**: Small, focused abstractions with clear responsibilities
- **Protocol-based**: Duck typing via Protocol for backend flexibility
