"""Tests for backend implementations."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from gitops_reconciler.backends import (
    AnsibleBackend,
    BackEnd,
    CloudFormationBackend,
    ComposeBackend,
    PiBackend,
    PulumiBackend,
    TerraformBackend,
    build_backend,
)
from gitops_reconciler.models import (
    AnsibleConfig,
    ApplyResult,
    CloudFormationConfig,
    ComposeConfig,
    PiConfig,
    PulumiConfig,
    TerraformConfig,
)


class TestTerraformBackend:
    """Tests for TerraformBackend."""

    def test_apply_no_change(self):
        """Terraform apply with no changes returns NO_CHANGE."""
        config = TerraformConfig(workdir=Path("/srv/terraform"))
        backend = TerraformBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Apply complete! Resources: 0 added, 0 changed, 0 destroyed."
        mock_result.stderr = ""

        with patch("gitops_reconciler.backends._run", return_value=mock_result):
            status = backend.apply()

        assert status.result == ApplyResult.NO_CHANGE

    def test_apply_with_changes(self):
        """Terraform apply with changes returns CHANGED."""
        config = TerraformConfig(workdir=Path("/srv/terraform"))
        backend = TerraformBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Apply complete! Resources: 2 added, 1 changed, 0 destroyed."
        mock_result.stderr = ""

        with patch("gitops_reconciler.backends._run", return_value=mock_result):
            status = backend.apply()

        assert status.result == ApplyResult.CHANGED

    def test_apply_failure(self):
        """Terraform apply failure returns FAILED."""
        config = TerraformConfig(workdir=Path("/srv/terraform"))
        backend = TerraformBackend(config)

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: Invalid configuration"

        with patch("gitops_reconciler.backends._run", return_value=mock_result):
            status = backend.apply()

        assert status.result == ApplyResult.FAILED
        assert "Invalid configuration" in status.message

    def test_apply_with_var_file(self):
        """Terraform apply includes var-file when configured."""
        config = TerraformConfig(
            workdir=Path("/srv/terraform"), var_file=Path("/srv/terraform/prod.tfvars")
        )
        backend = TerraformBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Apply complete! Resources: 0 added, 0 changed, 0 destroyed."

        with patch("gitops_reconciler.backends._run", return_value=mock_result) as mock_run:
            backend.apply()
            called_cmd = mock_run.call_args[0][0]
            assert "-var-file" in called_cmd
            assert "/srv/terraform/prod.tfvars" in called_cmd

    def test_destroy(self):
        """Terraform destroy works."""
        config = TerraformConfig(workdir=Path("/srv/terraform"))
        backend = TerraformBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Destroy complete!"
        mock_result.stderr = ""

        with patch("gitops_reconciler.backends._run", return_value=mock_result):
            status = backend.destroy()

        assert status.result == ApplyResult.CHANGED

    def test_get_outputs(self):
        """Terraform outputs are parsed correctly."""
        config = TerraformConfig(workdir=Path("/srv/terraform"))
        backend = TerraformBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {
                "vpc_id": {"value": "vpc-123456"},
                "subnet_id": {"value": "subnet-789012"},
            }
        )

        with patch("gitops_reconciler.backends._run", return_value=mock_result):
            outputs = backend.get_outputs()

        assert outputs == {"vpc_id": "vpc-123456", "subnet_id": "subnet-789012"}


class TestPulumiBackend:
    """Tests for PulumiBackend."""

    def test_apply_no_change(self):
        """Pulumi up with no changes returns NO_CHANGE."""
        config = PulumiConfig(project_dir=Path("/srv/pulumi"), stack="dev")
        backend = PulumiBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Updating (dev):\nno changes\n"
        mock_result.stderr = ""

        with patch("gitops_reconciler.backends._run", return_value=mock_result):
            status = backend.apply()

        assert status.result == ApplyResult.NO_CHANGE

    def test_apply_with_changes(self):
        """Pulumi up with changes returns CHANGED."""
        config = PulumiConfig(project_dir=Path("/srv/pulumi"), stack="dev")
        backend = PulumiBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Updating (dev):\n  + 2 resources created\n"
        mock_result.stderr = ""

        with patch("gitops_reconciler.backends._run", return_value=mock_result):
            status = backend.apply()

        assert status.result == ApplyResult.CHANGED

    def test_apply_failure(self):
        """Pulumi up failure returns FAILED."""
        config = PulumiConfig(project_dir=Path("/srv/pulumi"), stack="dev")
        backend = PulumiBackend(config)

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stderr = "error: resource creation failed"

        with patch("gitops_reconciler.backends._run", return_value=mock_result):
            status = backend.apply()

        assert status.result == ApplyResult.FAILED

    def test_get_outputs(self):
        """Pulumi outputs are parsed correctly."""
        config = PulumiConfig(project_dir=Path("/srv/pulumi"), stack="dev")
        backend = PulumiBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"url": "https://example.com", "port": 8080})

        with patch("gitops_reconciler.backends._run", return_value=mock_result):
            outputs = backend.get_outputs()

        assert outputs == {"url": "https://example.com", "port": 8080}


class TestCloudFormationBackend:
    """Tests for CloudFormationBackend (stub implementation)."""

    def test_apply_stub(self):
        """CloudFormation apply returns stub message."""
        config = CloudFormationConfig(
            stack_name="my-stack",
            template_path=Path("/template.yaml"),
            region="us-east-1",
        )
        backend = CloudFormationBackend(config)
        status = backend.apply()

        assert status.result == ApplyResult.NO_CHANGE
        assert "stub" in status.message.lower()

    def test_destroy_stub(self):
        """CloudFormation destroy returns stub message."""
        config = CloudFormationConfig(
            stack_name="my-stack",
            template_path=Path("/template.yaml"),
            region="us-east-1",
        )
        backend = CloudFormationBackend(config)
        status = backend.destroy()

        assert status.result == ApplyResult.CHANGED

    def test_get_outputs_stub(self):
        """CloudFormation outputs returns empty dict."""
        config = CloudFormationConfig(
            stack_name="my-stack",
            template_path=Path("/template.yaml"),
            region="us-east-1",
        )
        backend = CloudFormationBackend(config)
        outputs = backend.get_outputs()

        assert outputs == {}


class TestComposeBackend:
    """Tests for ComposeBackend."""

    def test_apply_no_change_same_hash(self):
        """Docker Compose apply with same hash returns NO_CHANGE."""
        config = ComposeConfig(compose_file=Path("/srv/app/compose.yml"), host="local")
        backend = ComposeBackend(config)

        current_hash = "abc123"
        Path("/srv/app/.last_applied_hash")

        with patch("gitops_reconciler.backends._hash_file", return_value=current_hash):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "read_text", return_value=current_hash):
                    status = backend.apply()

        assert status.result == ApplyResult.NO_CHANGE

    def test_apply_changed_different_hash(self):
        """Docker Compose apply with different hash returns CHANGED."""
        config = ComposeConfig(compose_file=Path("/srv/app/compose.yml"), host="local")
        backend = ComposeBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Container started"
        mock_result.stderr = ""

        with patch("gitops_reconciler.backends._hash_file", return_value="new_hash"):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "read_text", return_value="old_hash"):
                    with patch.object(Path, "write_text") as mock_write:
                        with patch("gitops_reconciler.backends._run", return_value=mock_result):
                            status = backend.apply()

        assert status.result == ApplyResult.CHANGED
        mock_write.assert_called_once_with("new_hash")

    def test_apply_failure(self):
        """Docker Compose apply failure returns FAILED."""
        config = ComposeConfig(compose_file=Path("/srv/app/compose.yml"), host="local")
        backend = ComposeBackend(config)

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stderr = "compose file invalid"

        with patch("gitops_reconciler.backends._hash_file", return_value="hash"):
            with patch.object(Path, "exists", return_value=False):
                with patch("gitops_reconciler.backends._run", return_value=mock_result):
                    status = backend.apply()

        assert status.result == ApplyResult.FAILED

    def test_remote_host_ssh_prefix(self):
        """Docker Compose with remote host uses SSH."""
        config = ComposeConfig(compose_file=Path("/srv/app/compose.yml"), host="staging.internal")
        backend = ComposeBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Done"

        with patch("gitops_reconciler.backends._hash_file", return_value="hash"):
            with patch.object(Path, "exists", return_value=False):
                with patch("gitops_reconciler.backends._run", return_value=mock_result) as mock_run:
                    with patch.object(Path, "write_text"):
                        backend.apply()

                    called_cmd = mock_run.call_args[0][0]
                    assert called_cmd[0] == "ssh"
                    assert called_cmd[1] == "staging.internal"


class TestAnsibleBackend:
    """Tests for AnsibleBackend."""

    def test_apply_no_change(self):
        """Ansible playbook with no changes returns NO_CHANGE."""
        config = AnsibleConfig(playbook=Path("/playbook.yml"), inventory=Path("/hosts"))
        backend = AnsibleBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "PLAY RECAP: changed=0 unreachable=0 failed=0"
        mock_result.stderr = ""

        with patch("gitops_reconciler.backends._run", return_value=mock_result):
            status = backend.apply()

        assert status.result == ApplyResult.NO_CHANGE

    def test_apply_with_changes(self):
        """Ansible playbook with changes returns CHANGED."""
        config = AnsibleConfig(playbook=Path("/playbook.yml"), inventory=Path("/hosts"))
        backend = AnsibleBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "PLAY RECAP: changed=3 unreachable=0 failed=0"
        mock_result.stderr = ""

        with patch("gitops_reconciler.backends._run", return_value=mock_result):
            status = backend.apply()

        assert status.result == ApplyResult.CHANGED

    def test_apply_with_extra_vars(self):
        """Ansible playbook includes extra vars."""
        config = AnsibleConfig(
            playbook=Path("/playbook.yml"),
            inventory=Path("/hosts"),
            extra_vars={"env": "prod", "version": "1.2.3"},
        )
        backend = AnsibleBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "changed=0"

        with patch("gitops_reconciler.backends._run", return_value=mock_result) as mock_run:
            backend.apply()
            called_cmd = mock_run.call_args[0][0]
            assert "-e" in called_cmd
            assert "env=prod" in called_cmd or "version=1.2.3" in called_cmd

    def test_destroy_not_supported(self):
        """Ansible backend destroy returns FAILED with message."""
        config = AnsibleConfig(playbook=Path("/playbook.yml"), inventory=Path("/hosts"))
        backend = AnsibleBackend(config)

        status = backend.destroy()
        assert status.result == ApplyResult.FAILED
        assert "no generic destroy" in status.message.lower()


class TestPiBackend:
    """Tests for PiBackend."""

    def test_apply_no_change_same_hash(self):
        """Pi backend with same content hash returns NO_CHANGE."""
        config = PiConfig(host="local", repo_path=Path("/repo"))
        backend = PiBackend(config)

        current_hash = "repo_hash_123"

        with patch.object(backend, "_content_hash", return_value=current_hash):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "read_text", return_value=current_hash):
                    status = backend.apply()

        assert status.result == ApplyResult.NO_CHANGE

    def test_apply_changed_different_hash(self):
        """Pi backend with different hash returns CHANGED."""
        config = PiConfig(host="local", repo_path=Path("/repo"))
        backend = PiBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Containers started"
        mock_result.stderr = ""

        with patch.object(backend, "_content_hash", return_value="new_hash"):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "read_text", return_value="old_hash"):
                    with patch.object(Path, "write_text") as mock_write:
                        with patch("gitops_reconciler.backends._run", return_value=mock_result):
                            status = backend.apply()

        assert status.result == ApplyResult.CHANGED
        mock_write.assert_called_once_with("new_hash")

    def test_custom_apply_command(self):
        """Pi backend uses custom apply command."""
        config = PiConfig(
            host="local",
            repo_path=Path("/repo"),
            apply_command=["systemctl", "restart", "myapp"],
        )
        backend = PiBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Service restarted"

        with patch.object(backend, "_content_hash", return_value="hash"):
            with patch.object(Path, "exists", return_value=False):
                with patch("gitops_reconciler.backends._run", return_value=mock_result) as mock_run:
                    with patch.object(Path, "write_text"):
                        backend.apply()

                    called_cmd = mock_run.call_args[0][0]
                    assert called_cmd == ["systemctl", "restart", "myapp"]

    def test_remote_host_uses_ssh(self):
        """Pi backend with remote host uses SSH."""
        config = PiConfig(host="pi.lan", repo_path=Path("/repo"))
        backend = PiBackend(config)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Done"

        with patch.object(backend, "_content_hash", return_value="hash"):
            with patch.object(Path, "exists", return_value=False):
                with patch("gitops_reconciler.backends._run", return_value=mock_result) as mock_run:
                    with patch.object(Path, "write_text"):
                        backend.apply()

                    called_cmd = mock_run.call_args[0][0]
                    assert called_cmd[0] == "ssh"
                    assert called_cmd[1] == "pi.lan"


class TestBackEndABC:
    """Tests for BackEnd abstract base class."""

    def test_all_backends_inherit_from_backend(self):
        """All backend classes inherit from BackEnd ABC."""
        backends = [
            TerraformBackend,
            PulumiBackend,
            CloudFormationBackend,
            ComposeBackend,
            AnsibleBackend,
            PiBackend,
        ]
        for backend_class in backends:
            assert issubclass(backend_class, BackEnd), (
                f"{backend_class.__name__} must inherit from BackEnd"
            )

    def test_backend_is_abc(self):
        """BackEnd cannot be instantiated directly."""
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            BackEnd()  # type: ignore[abstract]


class TestBuildBackend:
    """Tests for the build_backend factory function."""

    def test_build_terraform(self):
        """build_backend creates TerraformBackend from TerraformConfig."""
        config = TerraformConfig(workdir=Path("/tf"))
        backend = build_backend(config)
        assert isinstance(backend, TerraformBackend)
        assert isinstance(backend, BackEnd)

    def test_build_pulumi(self):
        """build_backend creates PulumiBackend from PulumiConfig."""
        config = PulumiConfig(project_dir=Path("/pulumi"), stack="dev")
        backend = build_backend(config)
        assert isinstance(backend, PulumiBackend)

    def test_build_cloudformation(self):
        """build_backend creates CloudFormationBackend from CloudFormationConfig."""
        config = CloudFormationConfig(
            stack_name="stack", template_path=Path("/template.yaml"), region="us-east-1"
        )
        backend = build_backend(config)
        assert isinstance(backend, CloudFormationBackend)

    def test_build_compose(self):
        """build_backend creates ComposeBackend from ComposeConfig."""
        config = ComposeConfig(compose_file=Path("/compose.yml"), host="local")
        backend = build_backend(config)
        assert isinstance(backend, ComposeBackend)

    def test_build_ansible(self):
        """build_backend creates AnsibleBackend from AnsibleConfig."""
        config = AnsibleConfig(playbook=Path("/playbook.yml"), inventory=Path("/hosts"))
        backend = build_backend(config)
        assert isinstance(backend, AnsibleBackend)

    def test_build_pi(self):
        """build_backend creates PiBackend from PiConfig."""
        config = PiConfig(host="local", repo_path=Path("/repo"))
        backend = build_backend(config)
        assert isinstance(backend, PiBackend)

    def test_build_unknown_type(self):
        """build_backend raises TypeError for unknown config type."""
        with pytest.raises(TypeError, match="no backend registered"):
            build_backend("not a valid config")
