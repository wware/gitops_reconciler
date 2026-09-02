"""The `BackEnd` ABC and its concrete implementations.

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
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

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


class BackEnd(ABC):
    """Abstract base class for all backend implementations.

    All backends must implement three methods:
    - apply(): Reconcile desired state, returning status
    - destroy(): Tear down all managed resources
    - get_outputs(): Return backend-specific output data
    """

    @abstractmethod
    def apply(self) -> Status:
        """Apply the desired state. Returns status indicating change/no-change/failure."""
        ...

    @abstractmethod
    def destroy(self) -> Status:
        """Destroy all managed resources. Returns status."""
        ...

    @abstractmethod
    def get_outputs(self) -> dict[str, Any]:
        """Get backend-specific outputs (e.g., terraform outputs, stack outputs)."""
        ...


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a shell command and return the completed process.

    Args:
        cmd: Command and arguments as a list of strings
        cwd: Optional working directory for the command

    Returns:
        CompletedProcess with stdout, stderr, and returncode
    """
    logger.debug("running: %s (cwd=%s)", " ".join(cmd), cwd)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def _hash_file(path: Path) -> str:
    """Compute SHA256 hash of a file's contents.

    Args:
        path: Path to the file to hash

    Returns:
        Hexadecimal digest of the file's SHA256 hash
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TerraformBackend(BackEnd):
    """Terraform/OpenTofu backend implementation.

    Idempotency is native: `terraform apply -auto-approve` performs a diff before
    mutating resources, making NO_CHANGE detection free.

    Attributes:
        _cfg: Frozen TerraformConfig with workdir, var_file, and workspace settings
    """

    def __init__(self, config: TerraformConfig) -> None:
        """Initialize the Terraform backend.

        Args:
            config: TerraformConfig with workdir and optional var_file/workspace
        """
        self._cfg = config

    def apply(self) -> Status:
        """Apply Terraform configuration, auto-approving changes.

        Returns:
            Status with CHANGED if resources were modified, NO_CHANGE if already
            converged, or FAILED with stderr on error.
        """
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
        """Destroy all Terraform-managed resources.

        Returns:
            Status with CHANGED on success or FAILED on error.
        """
        result = _run(["terraform", "destroy", "-auto-approve", "-no-color"], cwd=self._cfg.workdir)
        return Status(
            result=ApplyResult.FAILED if result.returncode else ApplyResult.CHANGED,
            message=(result.stdout + result.stderr)[-2000:],
        )

    def get_outputs(self) -> dict[str, Any]:
        """Get Terraform output values.

        Returns:
            Dictionary mapping output names to their values, or empty dict on error.
        """
        result = _run(["terraform", "output", "-json"], cwd=self._cfg.workdir)
        if result.returncode != 0:
            return {}
        return {k: v.get("value") for k, v in json.loads(result.stdout).items()}


class PulumiBackend(BackEnd):
    """Pulumi backend implementation via CLI (subprocess).

    Note: Production use should leverage the Pulumi Automation API
    (`pulumi.automation.create_or_select_stack`) for structured results
    instead of parsing CLI output. This subprocess implementation is
    dependency-free but less robust.

    The `--refresh` flag is critical: without it, Pulumi won't detect
    out-of-band drift.

    Attributes:
        _cfg: Frozen PulumiConfig with project_dir, stack, and optional backend_url
    """

    def __init__(self, config: PulumiConfig) -> None:
        """Initialize the Pulumi backend.

        Args:
            config: PulumiConfig with project directory and stack name
        """
        self._cfg = config

    def apply(self) -> Status:
        """Run pulumi up with refresh, auto-approving changes.

        Returns:
            Status with CHANGED if resources were modified, NO_CHANGE if
            already converged, or FAILED on error.
        """
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
        """Destroy all Pulumi-managed resources in the stack.

        Returns:
            Status with CHANGED on success or FAILED on error.
        """
        result = _run(["pulumi", "destroy", "-y", "-s", self._cfg.stack], cwd=self._cfg.project_dir)
        return Status(
            result=ApplyResult.FAILED if result.returncode else ApplyResult.CHANGED,
            message=(result.stdout + result.stderr)[-2000:],
        )

    def get_outputs(self) -> dict[str, Any]:
        """Get Pulumi stack output values.

        Returns:
            Dictionary mapping output names to their values, or empty dict on error.
        """
        result = _run(
            ["pulumi", "stack", "output", "-j", "-s", self._cfg.stack], cwd=self._cfg.project_dir
        )
        return json.loads(result.stdout) if result.returncode == 0 else {}


class CloudFormationBackend(BackEnd):
    """AWS CloudFormation backend (stub implementation).

    Production implementation should use boto3's `cloudformation` client:
    - create_change_set() / execute_change_set() for apply
    - describe_stacks() + waiters for status polling
    - Native change sets provide free diff/preview capability

    Attributes:
        _cfg: Frozen CloudFormationConfig with stack_name, template_path, region, parameters
    """

    def __init__(self, config: CloudFormationConfig) -> None:
        """Initialize the CloudFormation backend.

        Args:
            config: CloudFormationConfig with stack settings and template path
        """
        self._cfg = config

    def apply(self) -> Status:
        """Apply CloudFormation stack (stub - needs boto3 implementation).

        Returns:
            Status indicating stub state (currently always NO_CHANGE)
        """
        return Status(result=ApplyResult.NO_CHANGE, message="stub: implement via boto3")

    def destroy(self) -> Status:
        """Destroy CloudFormation stack (stub - needs boto3 implementation).

        Returns:
            Status indicating stub state (currently always CHANGED)
        """
        return Status(result=ApplyResult.CHANGED, message="stub: implement via boto3")

    def get_outputs(self) -> dict[str, Any]:
        """Get CloudFormation stack outputs (stub - needs boto3 implementation).

        Returns:
            Empty dictionary (stub implementation)
        """
        return {}


class ComposeBackend(BackEnd):
    """Docker Compose backend with hash-based idempotency.

    No native diff capability — idempotency is faked by hashing the compose
    file and comparing to the hash from the last successful apply. If the
    hash matches, returns NO_CHANGE without running `docker compose up`.

    Supports both local and remote (SSH) execution.

    Attributes:
        _cfg: Frozen ComposeConfig with compose_file path, host, and project_name
        _hash_marker: Path to hidden file storing the last applied hash
    """

    def __init__(self, config: ComposeConfig) -> None:
        """Initialize the Docker Compose backend.

        Args:
            config: ComposeConfig with compose file path and optional SSH host
        """
        self._cfg = config
        self._hash_marker = config.compose_file.parent / ".last_applied_hash"

    def _ssh_prefix(self) -> list[str]:
        """Generate SSH command prefix if remote host is configured.

        Returns:
            Empty list for local execution, ["ssh", host] for remote.
        """
        return [] if self._cfg.host in ("", "local") else ["ssh", self._cfg.host]

    def apply(self) -> Status:
        """Apply Docker Compose configuration if compose file changed.

        Compares current compose file hash to last applied hash. If unchanged,
        returns NO_CHANGE without executing `docker compose up`.

        Returns:
            Status with NO_CHANGE if hash matches, CHANGED if containers were
            updated, or FAILED on error.
        """
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
        """Tear down all containers defined in the compose file.

        Returns:
            Status with CHANGED on success or FAILED on error.
        """
        cmd = self._ssh_prefix() + ["docker", "compose", "-f", str(self._cfg.compose_file), "down"]
        result = _run(cmd)
        return Status(
            result=ApplyResult.FAILED if result.returncode else ApplyResult.CHANGED,
            message=(result.stdout + result.stderr)[-2000:],
        )

    def get_outputs(self) -> dict[str, Any]:
        """Get container status via `docker compose ps`.

        Returns:
            Dictionary with "ps" key containing JSON-formatted container list,
            or empty dict on error.
        """
        cmd = self._ssh_prefix() + [
            "docker",
            "compose",
            "-f",
            str(self._cfg.compose_file),
            "ps",
            "--format",
            "json",
        ]
        result = _run(cmd)
        return {"ps": result.stdout} if result.returncode == 0 else {}


class AnsibleBackend(BackEnd):
    """Ansible playbook backend with trust-based idempotency.

    Relies on playbooks being written idiomatically (using `changed_when`,
    proper modules, etc.). The reconciler trusts the `changed=N` count in
    Ansible's recap output to determine if changes were made.

    Idempotency quality depends entirely on playbook authorship—there's no
    external verification mechanism.

    Attributes:
        _cfg: Frozen AnsibleConfig with playbook, inventory, and extra_vars
    """

    def __init__(self, config: AnsibleConfig) -> None:
        """Initialize the Ansible backend.

        Args:
            config: AnsibleConfig with playbook path, inventory, and optional extra vars
        """
        self._cfg = config

    def apply(self) -> Status:
        """Run the Ansible playbook.

        Returns:
            Status with NO_CHANGE if `changed=0` in recap, CHANGED if tasks
            modified state, or FAILED on error.
        """
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
        """Ansible has no generic destroy operation.

        Returns:
            Status with FAILED and message directing user to write a teardown playbook.
        """
        return Status(
            result=ApplyResult.FAILED,
            message="AnsibleBackend has no generic destroy; write a teardown playbook",
        )

    def get_outputs(self) -> dict[str, Any]:
        """Ansible has no native output mechanism.

        Returns:
            Empty dictionary (no outputs available without custom fact registration)
        """
        return {}


class PiBackend(BackEnd):
    """Raspberry Pi / edge device backend with repo-wide content hashing.

    The simplest possible target and a good stress test of the abstraction—
    no cloud API, just a configurable apply command (e.g., `docker compose up`
    or `systemctl restart`).

    Uses hash-based idempotency like ComposeBackend, but hashes the entire
    repo tree instead of a single file, since a Pi's config repo may contain
    multiple compose files, systemd units, config files, etc.

    Git sync is handled by the wrapper; this backend only runs the apply command.

    Supports both local execution (wrapper runs on the Pi) and remote (SSH).

    Attributes:
        _cfg: Frozen PiConfig with host, repo_path, and apply_command
        _hash_marker: Path to hidden file storing last applied repo content hash
    """

    def __init__(self, config: PiConfig) -> None:
        """Initialize the Pi backend.

        Args:
            config: PiConfig with host, repo path, and apply command
        """
        self._cfg = config
        self._hash_marker = config.repo_path / ".last_applied_hash"

    def _ssh_prefix(self) -> list[str]:
        """Generate SSH command prefix if remote host is configured.

        Returns:
            Empty list for local execution, ["ssh", host] for remote.
        """
        return [] if self._cfg.host in ("", "local") else ["ssh", self._cfg.host]

    def _content_hash(self) -> str:
        """Compute SHA256 hash of entire repo tree (excluding .git and hash marker).

        Returns:
            Hexadecimal digest of the repo's content hash
        """
        digest = hashlib.sha256()
        for path in sorted(self._cfg.repo_path.rglob("*")):
            if path.is_file() and ".git" not in path.parts and path != self._hash_marker:
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def apply(self) -> Status:
        """Run the configured apply command if repo content changed.

        Compares current repo content hash to last applied hash. If unchanged,
        returns NO_CHANGE without running the apply command.

        Returns:
            Status with NO_CHANGE if hash matches, CHANGED if apply command
            succeeded, or FAILED on error.
        """
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
        """Tear down containers (assumes docker compose is in use).

        Returns:
            Status with CHANGED on success or FAILED on error.
        """
        cmd = self._ssh_prefix() + ["docker", "compose", "down"]
        cwd = self._cfg.repo_path if self._cfg.host == "local" else None
        result = _run(cmd, cwd=cwd)
        return Status(
            result=ApplyResult.FAILED if result.returncode else ApplyResult.CHANGED,
            message=(result.stdout + result.stderr)[-2000:],
        )

    def get_outputs(self) -> dict[str, Any]:
        """Get running container status via `docker ps`.

        Returns:
            Dictionary with "containers" key containing list of container status lines,
            or empty dict on error.
        """
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
    """Factory function that maps a config instance to its concrete backend.

    Args:
        config: One of the config types (TerraformConfig, PulumiConfig, etc.)

    Returns:
        Instantiated backend matching the config type

    Raises:
        TypeError: If config type has no registered backend builder

    Example:
        >>> config = TerraformConfig(workdir=Path("/srv/terraform"))
        >>> backend = build_backend(config)
        >>> isinstance(backend, TerraformBackend)
        True
    """
    try:
        builder = _BUILDERS[type(config)]  # type: ignore[index]
        return builder(config)  # type: ignore[no-any-return]
    except KeyError as exc:
        raise TypeError(f"no backend registered for config type {type(config).__name__}") from exc
