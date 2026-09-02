"""Tests for the staging -> production promotion pattern.

Exercises the pieces documented in PROMOTION.md: reading staging's last
recorded SHA via the wrapper's provenance tracking, and using it to update
prod's compose file pin the way promote.example.py does.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from gitops_reconciler import last_recorded_sha, record_last_sha
from gitops_reconciler.models import ApplyResult, Status

STAGING_SHA = "abc123f456deadbeef123456789abcdef0123456"


def _update_prod_pin(compose_file: Path, sha: str) -> None:
    """Mirror promote.example.py's regex-based pin update for testing."""
    import re

    content = compose_file.read_text()
    updated = re.sub(r"(image:\s*\S+:)\S+", rf"\g<1>{sha[:7]}", content)
    compose_file.write_text(updated)


class TestPromotionWorkflow:
    """End-to-end promotion workflow: staging SHA -> prod compose pin."""

    def test_promotion_workflow_end_to_end(self, tmp_path):
        """Staging's recorded SHA can be retrieved and used to update prod config."""
        mock_state_dir = MagicMock(spec=Path)
        state_files: dict[str, Path] = {}

        def fake_truediv(_self: Path, name: str) -> Path:
            state_files[name] = tmp_path / name
            return state_files[name]

        mock_state_dir.__truediv__ = fake_truediv
        mock_state_dir.mkdir.return_value = None

        with patch("gitops_reconciler.wrapper.STATE_DIR", mock_state_dir):
            record_last_sha(
                "demo-app-staging",
                STAGING_SHA,
                Status(result=ApplyResult.CHANGED, message="deployed"),
            )
            retrieved_sha = last_recorded_sha("demo-app-staging")

        assert retrieved_sha == STAGING_SHA

        prod_compose = tmp_path / "docker-compose.prod.yml"
        prod_compose.write_text("services:\n  demo-app:\n    image: gitops-demo-app:v1.0.0\n")

        _update_prod_pin(prod_compose, retrieved_sha)

        updated_content = prod_compose.read_text()
        assert STAGING_SHA[:7] in updated_content
        assert "v1.0.0" not in updated_content

    def test_last_recorded_sha_none_blocks_promotion(self, tmp_path):
        """A target that has never applied successfully has no SHA to promote."""
        mock_state_dir = MagicMock(spec=Path)
        mock_state_dir.__truediv__ = lambda self, name: tmp_path / name

        with patch("gitops_reconciler.wrapper.STATE_DIR", mock_state_dir):
            assert last_recorded_sha("never-applied-target") is None


class TestUpdateProdPin:
    """Tests for the compose-file pin rewrite used by the promotion script."""

    def test_update_prod_pin_replaces_tag_only(self, tmp_path):
        """Only the image tag changes; the repository name is preserved."""
        compose_file = tmp_path / "docker-compose.prod.yml"
        compose_file.write_text(
            "services:\n"
            "  demo-app:\n"
            "    image: gitops-demo-app:v1.0.0\n"
            "    ports:\n"
            '      - "9002:8080"\n'
        )

        _update_prod_pin(compose_file, STAGING_SHA)

        content = compose_file.read_text()
        assert "image: gitops-demo-app:abc123f\n" in content
        assert "9002:8080" in content  # untouched

    def test_update_prod_pin_idempotent_when_already_promoted(self, tmp_path):
        """Re-running promotion with the same SHA leaves the file unchanged."""
        compose_file = tmp_path / "docker-compose.prod.yml"
        compose_file.write_text("services:\n  demo-app:\n    image: gitops-demo-app:abc123f\n")

        _update_prod_pin(compose_file, STAGING_SHA)

        assert compose_file.read_text().count("abc123f") == 1
