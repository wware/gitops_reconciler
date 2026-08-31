"""Shared status type and per-backend configuration models.

Config models are deliberately NOT unified into one shared shape — see the
design doc's reasoning. Each backend's config is fully its own; the
discriminated union at the bottom is only for callers who want to load
target definitions from a single YAML/JSON file and dispatch by `kind`.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class ApplyResult(str, Enum):
    NO_CHANGE = "no_change"
    CHANGED = "changed"
    FAILED = "failed"


class Status(BaseModel, frozen=True):
    result: ApplyResult
    message: str = ""


class TerraformConfig(BaseModel, frozen=True):
    kind: Literal["terraform"] = "terraform"
    workdir: Path
    var_file: Path | None = None
    workspace: str = "default"


class PulumiConfig(BaseModel, frozen=True):
    kind: Literal["pulumi"] = "pulumi"
    project_dir: Path
    stack: str
    backend_url: str | None = None  # e.g. "s3://my-pulumi-state"


class CloudFormationConfig(BaseModel, frozen=True):
    kind: Literal["cloudformation"] = "cloudformation"
    stack_name: str
    template_path: Path
    region: str
    parameters: dict[str, str] = Field(default_factory=dict)


class ComposeConfig(BaseModel, frozen=True):
    kind: Literal["compose"] = "compose"
    compose_file: Path
    host: str  # SSH-able hostname/IP; "" or "local" for local execution
    project_name: str | None = None


class AnsibleConfig(BaseModel, frozen=True):
    kind: Literal["ansible"] = "ansible"
    playbook: Path
    inventory: Path
    extra_vars: dict[str, str] = Field(default_factory=dict)


class PiConfig(BaseModel, frozen=True):
    kind: Literal["pi"] = "pi"
    host: str  # "local" if the reconciler process runs on the Pi itself
    repo_path: Path  # checked-out config repo *on the Pi*
    apply_command: list[str] = Field(
        default_factory=lambda: ["docker", "compose", "up", "-d"]
    )


BackendConfig = Annotated[
    Union[
        TerraformConfig,
        PulumiConfig,
        CloudFormationConfig,
        ComposeConfig,
        AnsibleConfig,
        PiConfig,
    ],
    Field(discriminator="kind"),
]
