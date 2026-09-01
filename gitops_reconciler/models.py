"""Shared status type and per-backend configuration models.

Config models are deliberately NOT unified into one shared shape — see the
design doc's reasoning. Each backend's config is fully its own; the
discriminated union at the bottom is only for callers who want to load
target definitions from a single YAML/JSON file and dispatch by `kind`.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ApplyResult(StrEnum):
    """Outcome of a reconciliation attempt.

    All backends return exactly one of these three states after every apply()
    or destroy() operation. This tri-state model supports idempotent operations
    and clear error signaling.

    Attributes:
        NO_CHANGE: Backend ran successfully but made no modifications to actual state
        CHANGED: Backend successfully modified resources to match desired state
        FAILED: Backend encountered an error; actual state may be inconsistent
    """

    NO_CHANGE = "no_change"
    CHANGED = "changed"
    FAILED = "failed"


class Status(BaseModel, frozen=True):
    """Return value from every backend apply() and destroy() operation.

    Immutable status record that backends use to communicate both the outcome
    (via ApplyResult enum) and human-readable context (via message string).

    The wrapper uses this to:
    - Decide whether to record the git SHA (only on non-FAILED results)
    - Trigger notifications (only on FAILED results)
    - Log reconciliation outcomes

    Attributes:
        result: One of NO_CHANGE, CHANGED, or FAILED
        message: Optional human-readable detail (error messages, change summaries, etc.)
    """

    result: ApplyResult
    message: str = ""


class TerraformConfig(BaseModel, frozen=True):
    """Configuration for TerraformBackend.

    Supports standard Terraform/OpenTofu workflows with workspaces and variable files.
    The workdir should contain .tf files (or be a module root).

    Attributes:
        kind: Discriminator for BackendConfig union; always "terraform"
        workdir: Directory containing .tf files
        var_file: Optional path to .tfvars file for parameterized configs
        workspace: Terraform workspace name (default: "default")
    """

    kind: Literal["terraform"] = "terraform"
    workdir: Path
    var_file: Path | None = None
    workspace: str = "default"


class PulumiConfig(BaseModel, frozen=True):
    """Configuration for PulumiBackend.

    Supports Pulumi projects with custom state backends. The project_dir should
    contain Pulumi.yaml and your program code.

    Attributes:
        kind: Discriminator for BackendConfig union; always "pulumi"
        project_dir: Directory containing Pulumi.yaml and program files
        stack: Pulumi stack name (e.g., "dev", "staging", "prod")
        backend_url: Optional custom state backend URL (e.g., "s3://my-pulumi-state")
    """

    kind: Literal["pulumi"] = "pulumi"
    project_dir: Path
    stack: str
    backend_url: str | None = None  # e.g. "s3://my-pulumi-state"


class CloudFormationConfig(BaseModel, frozen=True):
    """Configuration for CloudFormationBackend.

    Manages AWS CloudFormation stacks with template-based infrastructure.
    Supports parameterized templates via the parameters dict.

    Attributes:
        kind: Discriminator for BackendConfig union; always "cloudformation"
        stack_name: CloudFormation stack name (must be unique per region)
        template_path: Path to CloudFormation template (YAML or JSON)
        region: AWS region (e.g., "us-east-1", "eu-west-1")
        parameters: Optional stack parameters as key-value pairs
    """

    kind: Literal["cloudformation"] = "cloudformation"
    stack_name: str
    template_path: Path
    region: str
    parameters: dict[str, str] = Field(default_factory=dict)


class ComposeConfig(BaseModel, frozen=True):
    """Configuration for ComposeBackend (docker-compose).

    Manages docker-compose deployments locally or over SSH. Uses hash-based
    idempotency since `docker compose up` doesn't natively detect no-change.

    Attributes:
        kind: Discriminator for BackendConfig union; always "compose"
        compose_file: Path to docker-compose.yml
        host: SSH-able hostname/IP; use "" or "local" for local execution
        project_name: Optional docker-compose project name override
    """

    kind: Literal["compose"] = "compose"
    compose_file: Path
    host: str  # SSH-able hostname/IP; "" or "local" for local execution
    project_name: str | None = None


class AnsibleConfig(BaseModel, frozen=True):
    """Configuration for AnsibleBackend.

    Runs Ansible playbooks for configuration management. Uses hash-based
    idempotency (hashing playbook + inventory + extra_vars) since Ansible
    doesn't expose a structured diff mode suitable for reconcilers.

    Attributes:
        kind: Discriminator for BackendConfig union; always "ansible"
        playbook: Path to Ansible playbook YAML file
        inventory: Path to Ansible inventory file
        extra_vars: Optional extra variables to pass to ansible-playbook
    """

    kind: Literal["ansible"] = "ansible"
    playbook: Path
    inventory: Path
    extra_vars: dict[str, str] = Field(default_factory=dict)


class PiConfig(BaseModel, frozen=True):
    """Configuration for PiBackend (Raspberry Pi / edge devices).

    The simplest backend - just runs a shell command on a target host. Useful for
    edge devices, home labs, or any scenario where you want GitOps without cloud APIs.

    Uses repo-wide content hashing for idempotency since the apply command is arbitrary.
    Git sync is handled by the wrapper; this backend only executes the apply command.

    Attributes:
        kind: Discriminator for BackendConfig union; always "pi"
        host: Target hostname/IP for SSH; use "local" if reconciler runs on the device
        repo_path: Path to checked-out config repo on the target device
        apply_command: Command to run (default: ["docker", "compose", "up", "-d"])
    """

    kind: Literal["pi"] = "pi"
    host: str  # "local" if the reconciler process runs on the Pi itself
    repo_path: Path  # checked-out config repo *on the Pi*
    apply_command: list[str] = Field(default_factory=lambda: ["docker", "compose", "up", "-d"])


BackendConfig = Annotated[
    TerraformConfig
    | PulumiConfig
    | CloudFormationConfig
    | ComposeConfig
    | AnsibleConfig
    | PiConfig,
    Field(discriminator="kind"),
]
"""Discriminated union of all backend configuration types.

Use this type when loading target definitions from config files (YAML/JSON) where
the backend type is determined at runtime via the 'kind' field. Pydantic will
automatically parse to the correct config class based on the discriminator.

Example:
    ```python
    import json
    from pydantic import TypeAdapter

    adapter = TypeAdapter(BackendConfig)
    config = adapter.validate_python(json.loads(config_json))
    # config is now a TerraformConfig, PulumiConfig, etc. based on 'kind' field
    ```

Not needed if you're instantiating backends directly in code - just use the
specific config class (TerraformConfig, PulumiConfig, etc.) and pass to
build_backend().
"""
