"""The `BackEnd` Protocol and its concrete implementations.

Terraform/Pulumi/CloudFormation get idempotency for free from their own
engines. Compose/Ansible/Pi don't — those fake a "no drift" check by
hashing rendered state and comparing to the hash from the last successful
apply. That asymmetry is real signal about which backends are cheap to
add and which require you to build the diff yourself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Protocol

from .models import (
    AnsibleConfig,
    ApplyResult,
    CloudFormationConfig,
    ComposeConfig,
    PiConfig,
    PulumiConfig,
    Status,
    TerraformConfig,
)

logger = logging.getLogger("gitops_reconciler.backends")


class BackEnd(Protocol):
    def apply(self) -> Status: ...
    def destroy(self) -> Status: ...
    def get_outputs(self) -> dict[str, Any]: ...


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    logger.debug("running: %s (cwd=%s)", " ".join(cmd), cwd)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TerraformBackend:
    """Idempotency is native: `apply -auto-approve` diffs before mutating."""

    def __init__(self, config: TerraformConfig) -> None:
        self._cfg = config

    def apply(self) -> Status:
        cmd = ["terraform", "apply", "-auto-approve", "-no-color"]
        if self._cfg.var_file:
            cmd += ["-var-file", str(self._cfg.var_file)]
        result = _run(cmd, cwd=self._cfg.workdir)
        if result.returncode != 0:
            return Status(result=ApplyResult.FAILED, message=result.stderr[-2000:])
        changed = "0 added, 0 changed, 0 destroyed" not in result.stdout
        return Status(
            result=ApplyResult.CHANGED if changed else ApplyResult.NO_CHANGE,
            message=result.stdout[-2000:],
        )

    def destroy(self) -> Status:
        result = _run(
            ["terraform", "destroy", "-auto-approve", "-no-color"], cwd=self._cfg.workdir
        )
        return Status(
            result=ApplyResult.FAILED if result.returncode else ApplyResult.CHANGED,
            message=(result.stdout + result.stderr)[-2000:],
        )

    def get_outputs(self) -> dict[str, Any]:
        result = _run(["terraform", "output", "-json"], cwd=self._cfg.workdir)
        if result.returncode != 0:
            return {}
        return {k: v.get("value") for k, v in json.loads(result.stdout).items()}


class PulumiBackend:
    """Sketch only. A real implementation should use the Automation API
    (`pulumi.automation.create_or_select_stack`) instead of shelling out to
    the CLI, for structured results and to avoid parsing human-readable
    output. Left as subprocess calls here to keep this skeleton
    dependency-free.
    """

    def __init__(self, config: PulumiConfig) -> None:
        self._cfg = config

    def apply(self) -> Status:
        cmd = ["pulumi", "up", "--refresh", "-y", "-s", self._cfg.stack]
        result = _run(cmd, cwd=self._cfg.project_dir)
        if result.returncode != 0:
            return Status(result=ApplyResult.FAILED, message=result.stderr[-2000:])
        changed = "no changes" not in result.stdout.lower()
        return Status(
            result=ApplyResult.CHANGED if changed else ApplyResult.NO_CHANGE,
            message=result.stdout[-2000:],
        )

    def destroy(self) -> Status:
        result = _run(["pulumi", "destroy", "-y", "-s", self._cfg.stack], cwd=self._cfg.project_dir)
        return Status(
            result=ApplyResult.FAILED if result.returncode else ApplyResult.CHANGED,
            message=(result.stdout + result.stderr)[-2000:],
        )

    def get_outputs(self) -> dict[str, Any]:
        result = _run(
            ["pulumi", "stack", "output", "-j", "-s", self._cfg.stack], cwd=self._cfg.project_dir
        )
        return json.loads(result.stdout) if result.returncode == 0 else {}


class CloudFormationBackend:
    """Sketch only. A real implementation should use boto3's
    `cloudformation` client (create_change_set / execute_change_set /
    describe_stacks + waiters) rather than the AWS CLI.
    """

    def __init__(self, config: CloudFormationConfig) -> None:
        self._cfg = config

    def apply(self) -> Status:
        return Status(result=ApplyResult.NO_CHANGE, message="stub: implement via boto3")

    def destroy(self) -> Status:
        return Status(result=ApplyResult.CHANGED, message="stub: implement via boto3")

    def get_outputs(self) -> dict[str, Any]:
        return {}


class ComposeBackend:
    """No native diff — 'no change' is faked by hashing the compose file
    and comparing to the hash recorded after the last successful apply.
    """

    def __init__(self, config: ComposeConfig) -> None:
        self._cfg = config
        self._hash_marker = config.compose_file.parent / ".last_applied_hash"

    def _ssh_prefix(self) -> list[str]:
        return [] if self._cfg.host in ("", "local") else ["ssh", self._cfg.host]

    def apply(self) -> Status:
        current_hash = _hash_file(self._cfg.compose_file)
        previous_hash = (
            self._hash_marker.read_text().strip() if self._hash_marker.exists() else None
        )
        if current_hash == previous_hash:
            return Status(result=ApplyResult.NO_CHANGE)

        cmd = self._ssh_prefix() + ["docker", "compose", "-f", str(self._cfg.compose_file)]
        if self._cfg.project_name:
            cmd += ["-p", self._cfg.project_name]
        cmd += ["up", "-d"]
        result = _run(cmd)
        if result.returncode != 0:
            return Status(result=ApplyResult.FAILED, message=result.stderr[-2000:])
        self._hash_marker.write_text(current_hash)
        return Status(result=ApplyResult.CHANGED, message=result.stdout[-2000:])

    def destroy(self) -> Status:
        cmd = self._ssh_prefix() + ["docker", "compose", "-f", str(self._cfg.compose_file), "down"]
        result = _run(cmd)
        return Status(
            result=ApplyResult.FAILED if result.returncode else ApplyResult.CHANGED,
            message=(result.stdout + result.stderr)[-2000:],
        )

    def get_outputs(self) -> dict[str, Any]:
        cmd = self._ssh_prefix() + [
            "docker", "compose", "-f", str(self._cfg.compose_file), "ps", "--format", "json",
        ]
        result = _run(cmd)
        return {"ps": result.stdout} if result.returncode == 0 else {}


class AnsibleBackend:
    """Same faked-idempotency caveat as ComposeBackend, unless the playbook
    itself is written to be genuinely idempotent — the reconciler has no
    way to verify that from the outside, so it trusts `changed=N` in the
    recap line.
    """

    def __init__(self, config: AnsibleConfig) -> None:
        self._cfg = config

    def apply(self) -> Status:
        cmd = ["ansible-playbook", "-i", str(self._cfg.inventory), str(self._cfg.playbook)]
        for k, v in self._cfg.extra_vars.items():
            cmd += ["-e", f"{k}={v}"]
        result = _run(cmd)
        if result.returncode != 0:
            return Status(result=ApplyResult.FAILED, message=result.stderr[-2000:])
        changed = "changed=0" not in result.stdout
        return Status(
            result=ApplyResult.CHANGED if changed else ApplyResult.NO_CHANGE,
            message=result.stdout[-2000:],
        )

    def destroy(self) -> Status:
        return Status(
            result=ApplyResult.FAILED,
            message="AnsibleBackend has no generic destroy; write a teardown playbook",
        )

    def get_outputs(self) -> dict[str, Any]:
        return {}


class PiBackend:
    """The cheapest possible target and a good stress test of the
    abstraction: no cloud API, just `git pull` (handled by the wrapper)
    plus a fixed apply command, with the same fake-idempotency hash marker
    as ComposeBackend — but hashed over the whole repo tree, since a Pi's
    desired-state repo may hold more than one compose file.
    """

    def __init__(self, config: PiConfig) -> None:
        self._cfg = config
        self._hash_marker = config.repo_path / ".last_applied_hash"

    def _ssh_prefix(self) -> list[str]:
        return [] if self._cfg.host in ("", "local") else ["ssh", self._cfg.host]

    def _content_hash(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self._cfg.repo_path.rglob("*")):
            if path.is_file() and ".git" not in path.parts:
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def apply(self) -> Status:
        current_hash = self._content_hash()
        previous_hash = (
            self._hash_marker.read_text().strip() if self._hash_marker.exists() else None
        )
        if current_hash == previous_hash:
            return Status(result=ApplyResult.NO_CHANGE)

        cmd = self._ssh_prefix() + self._cfg.apply_command
        cwd = self._cfg.repo_path if self._cfg.host == "local" else None
        result = _run(cmd, cwd=cwd)
        if result.returncode != 0:
            return Status(result=ApplyResult.FAILED, message=result.stderr[-2000:])
        self._hash_marker.write_text(current_hash)
        return Status(result=ApplyResult.CHANGED, message=result.stdout[-2000:])

    def destroy(self) -> Status:
        cmd = self._ssh_prefix() + ["docker", "compose", "down"]
        cwd = self._cfg.repo_path if self._cfg.host == "local" else None
        result = _run(cmd, cwd=cwd)
        return Status(
            result=ApplyResult.FAILED if result.returncode else ApplyResult.CHANGED,
            message=(result.stdout + result.stderr)[-2000:],
        )

    def get_outputs(self) -> dict[str, Any]:
        cmd = self._ssh_prefix() + ["docker", "ps", "--format", "{{.Names}}: {{.Status}}"]
        cwd = self._cfg.repo_path if self._cfg.host == "local" else None
        result = _run(cmd, cwd=cwd)
        return {"containers": result.stdout.splitlines()} if result.returncode == 0 else {}


_BUILDERS = {
    TerraformConfig: TerraformBackend,
    PulumiConfig: PulumiBackend,
    CloudFormationConfig: CloudFormationBackend,
    ComposeConfig: ComposeBackend,
    AnsibleConfig: AnsibleBackend,
    PiConfig: PiBackend,
}


def build_backend(config: Any) -> BackEnd:
    """Factory: maps a config instance to its concrete backend by type."""
    try:
        return _BUILDERS[type(config)](config)
    except KeyError:
        raise TypeError(f"no backend registered for config type {type(config).__name__}")
