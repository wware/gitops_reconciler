"""Tests for pydantic models and configuration types."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from gitops_reconciler.models import (
    AnsibleConfig,
    ApplyResult,
    BackendConfig,
    CloudFormationConfig,
    ComposeConfig,
    PiConfig,
    PulumiConfig,
    Status,
    TerraformConfig,
)


class TestApplyResult:
    """Tests for the ApplyResult enum."""

    def test_all_values_present(self):
        """Ensure all expected enum values exist."""
        assert ApplyResult.NO_CHANGE.value == "no_change"
        assert ApplyResult.CHANGED.value == "changed"
        assert ApplyResult.FAILED.value == "failed"

    def test_enum_membership(self):
        """Test enum value membership."""
        assert "no_change" in [r.value for r in ApplyResult]
        assert "changed" in [r.value for r in ApplyResult]
        assert "failed" in [r.value for r in ApplyResult]


class TestStatus:
    """Tests for the Status model."""

    def test_status_creation_minimal(self):
        """Status can be created with just result."""
        status = Status(result=ApplyResult.NO_CHANGE)
        assert status.result == ApplyResult.NO_CHANGE
        assert status.message == ""

    def test_status_creation_with_message(self):
        """Status can be created with result and message."""
        status = Status(result=ApplyResult.FAILED, message="Something went wrong")
        assert status.result == ApplyResult.FAILED
        assert status.message == "Something went wrong"

    def test_status_is_frozen(self):
        """Status model is immutable."""
        status = Status(result=ApplyResult.CHANGED)
        with pytest.raises(ValidationError):
            status.result = ApplyResult.FAILED  # type: ignore


class TestTerraformConfig:
    """Tests for TerraformConfig model."""

    def test_minimal_config(self):
        """Terraform config with just workdir."""
        config = TerraformConfig(workdir=Path("/srv/terraform"))
        assert config.kind == "terraform"
        assert config.workdir == Path("/srv/terraform")
        assert config.var_file is None
        assert config.workspace == "default"

    def test_full_config(self):
        """Terraform config with all options."""
        config = TerraformConfig(
            workdir=Path("/srv/terraform"),
            var_file=Path("/srv/terraform/prod.tfvars"),
            workspace="production",
        )
        assert config.workdir == Path("/srv/terraform")
        assert config.var_file == Path("/srv/terraform/prod.tfvars")
        assert config.workspace == "production"

    def test_config_is_frozen(self):
        """TerraformConfig is immutable."""
        config = TerraformConfig(workdir=Path("/srv/terraform"))
        with pytest.raises(ValidationError):
            config.workspace = "staging"  # type: ignore


class TestPulumiConfig:
    """Tests for PulumiConfig model."""

    def test_minimal_config(self):
        """Pulumi config with required fields only."""
        config = PulumiConfig(project_dir=Path("/srv/pulumi"), stack="dev")
        assert config.kind == "pulumi"
        assert config.project_dir == Path("/srv/pulumi")
        assert config.stack == "dev"
        assert config.backend_url is None

    def test_with_backend_url(self):
        """Pulumi config with custom backend URL."""
        config = PulumiConfig(
            project_dir=Path("/srv/pulumi"),
            stack="prod",
            backend_url="s3://my-pulumi-state",
        )
        assert config.backend_url == "s3://my-pulumi-state"


class TestCloudFormationConfig:
    """Tests for CloudFormationConfig model."""

    def test_minimal_config(self):
        """CloudFormation config with required fields."""
        config = CloudFormationConfig(
            stack_name="my-stack",
            template_path=Path("/srv/cfn/template.yaml"),
            region="us-east-1",
        )
        assert config.kind == "cloudformation"
        assert config.stack_name == "my-stack"
        assert config.template_path == Path("/srv/cfn/template.yaml")
        assert config.region == "us-east-1"
        assert config.parameters == {}

    def test_with_parameters(self):
        """CloudFormation config with parameters."""
        config = CloudFormationConfig(
            stack_name="my-stack",
            template_path=Path("/srv/cfn/template.yaml"),
            region="us-west-2",
            parameters={"Environment": "production", "InstanceType": "t3.micro"},
        )
        assert config.parameters == {"Environment": "production", "InstanceType": "t3.micro"}


class TestComposeConfig:
    """Tests for ComposeConfig model."""

    def test_local_compose(self):
        """Docker Compose config for local execution."""
        config = ComposeConfig(
            compose_file=Path("/srv/app/docker-compose.yml"),
            host="local",
        )
        assert config.kind == "compose"
        assert config.compose_file == Path("/srv/app/docker-compose.yml")
        assert config.host == "local"
        assert config.project_name is None

    def test_remote_compose(self):
        """Docker Compose config for remote host."""
        config = ComposeConfig(
            compose_file=Path("/srv/app/docker-compose.yml"),
            host="staging.internal",
            project_name="myapp",
        )
        assert config.host == "staging.internal"
        assert config.project_name == "myapp"


class TestAnsibleConfig:
    """Tests for AnsibleConfig model."""

    def test_minimal_config(self):
        """Ansible config with required fields."""
        config = AnsibleConfig(
            playbook=Path("/srv/ansible/site.yml"),
            inventory=Path("/srv/ansible/hosts.ini"),
        )
        assert config.kind == "ansible"
        assert config.playbook == Path("/srv/ansible/site.yml")
        assert config.inventory == Path("/srv/ansible/hosts.ini")
        assert config.extra_vars == {}

    def test_with_extra_vars(self):
        """Ansible config with extra variables."""
        config = AnsibleConfig(
            playbook=Path("/srv/ansible/site.yml"),
            inventory=Path("/srv/ansible/hosts.ini"),
            extra_vars={"env": "production", "version": "1.2.3"},
        )
        assert config.extra_vars == {"env": "production", "version": "1.2.3"}


class TestPiConfig:
    """Tests for PiConfig model."""

    def test_minimal_local_config(self):
        """Pi config for local execution with defaults."""
        config = PiConfig(
            host="local",
            repo_path=Path("/home/pi/gitops-config"),
        )
        assert config.kind == "pi"
        assert config.host == "local"
        assert config.repo_path == Path("/home/pi/gitops-config")
        assert config.apply_command == ["docker", "compose", "up", "-d"]

    def test_remote_with_custom_command(self):
        """Pi config with custom apply command."""
        config = PiConfig(
            host="pi-livingroom.lan",
            repo_path=Path("/home/pi/config"),
            apply_command=["systemctl", "restart", "myapp"],
        )
        assert config.host == "pi-livingroom.lan"
        assert config.apply_command == ["systemctl", "restart", "myapp"]


class TestBackendConfig:
    """Tests for the discriminated union BackendConfig."""

    def test_terraform_discrimination(self):
        """BackendConfig properly discriminates Terraform."""
        config: BackendConfig = TerraformConfig(workdir=Path("/srv/tf"))
        assert isinstance(config, TerraformConfig)
        assert config.kind == "terraform"

    def test_pulumi_discrimination(self):
        """BackendConfig properly discriminates Pulumi."""
        config: BackendConfig = PulumiConfig(project_dir=Path("/srv/pulumi"), stack="dev")
        assert isinstance(config, PulumiConfig)
        assert config.kind == "pulumi"

    def test_cloudformation_discrimination(self):
        """BackendConfig properly discriminates CloudFormation."""
        config: BackendConfig = CloudFormationConfig(
            stack_name="stack", template_path=Path("/template.yaml"), region="us-east-1"
        )
        assert isinstance(config, CloudFormationConfig)
        assert config.kind == "cloudformation"

    def test_compose_discrimination(self):
        """BackendConfig properly discriminates Compose."""
        config: BackendConfig = ComposeConfig(compose_file=Path("/compose.yml"), host="local")
        assert isinstance(config, ComposeConfig)
        assert config.kind == "compose"

    def test_ansible_discrimination(self):
        """BackendConfig properly discriminates Ansible."""
        config: BackendConfig = AnsibleConfig(
            playbook=Path("/playbook.yml"), inventory=Path("/hosts")
        )
        assert isinstance(config, AnsibleConfig)
        assert config.kind == "ansible"

    def test_pi_discrimination(self):
        """BackendConfig properly discriminates Pi."""
        config: BackendConfig = PiConfig(host="local", repo_path=Path("/repo"))
        assert isinstance(config, PiConfig)
        assert config.kind == "pi"
