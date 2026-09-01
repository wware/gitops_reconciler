"""Tests for the reconciliation wrapper logic."""

import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, mock_open, patch

import pytest

from gitops_reconciler.models import ApplyResult, Status
from gitops_reconciler.wrapper import (
    ManagedTarget,
    backend_lock,
    current_sha,
    default_notifier,
    last_recorded_sha,
    record_last_sha,
    run,
    sync_git,
    tick,
)


class MockBackend:
    """A simple mock backend for testing."""

    def __init__(self, status: Status) -> None:
        self._status = status
        self.apply_called = False
        self.destroy_called = False

    def apply(self) -> Status:
        self.apply_called = True
        return self._status

    def destroy(self) -> Status:
        self.destroy_called = True
        return self._status

    def get_outputs(self) -> dict[str, object]:
        return {}


class TestBackendLock:
    """Tests for the backend_lock context manager."""

    def test_lock_acquired_successfully(self):
        """Lock can be acquired when not held."""
        with patch("gitops_reconciler.wrapper.LOCK_DIR", Path("/tmp/test-locks")):
            with patch("builtins.open", mock_open()):
                with patch("fcntl.flock") as mock_flock:
                    with backend_lock("test-target") as acquired:
                        assert acquired is True
                        mock_flock.assert_called_once()

    def test_lock_not_acquired_when_busy(self):
        """Lock acquisition fails when already held."""

        with patch("gitops_reconciler.wrapper.LOCK_DIR", Path("/tmp/test-locks")):
            with patch("builtins.open", mock_open()):
                with patch("fcntl.flock", side_effect=BlockingIOError):
                    with backend_lock("test-target") as acquired:
                        assert acquired is False

    def test_lock_creates_directory(self):
        """Lock directory is created if it doesn't exist."""
        mock_lock_dir = MagicMock(spec=Path)
        with patch("gitops_reconciler.wrapper.LOCK_DIR", mock_lock_dir):
            with patch("builtins.open", mock_open()):
                with patch("fcntl.flock"):
                    with backend_lock("test-target"):
                        mock_lock_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)


class TestSyncGit:
    """Tests for git synchronization."""

    def test_sync_git_fetch_and_reset(self):
        """sync_git calls fetch and reset."""
        repo = Path("/srv/repo")

        with patch("subprocess.run") as mock_run:
            sync_git(repo)

            assert mock_run.call_count == 2
            # First call: fetch
            assert mock_run.call_args_list[0][0][0] == [
                "git",
                "-C",
                "/srv/repo",
                "fetch",
                "--quiet",
            ]
            # Second call: reset
            assert mock_run.call_args_list[1][0][0] == [
                "git",
                "-C",
                "/srv/repo",
                "reset",
                "--hard",
                "origin/main",
            ]


class TestCurrentSha:
    """Tests for current_sha function."""

    def test_current_sha_returns_sha(self):
        """current_sha returns the git SHA."""
        repo = Path("/srv/repo")
        expected_sha = "abc123def456"

        mock_result = Mock()
        mock_result.stdout = f"{expected_sha}\n"

        with patch("subprocess.run", return_value=mock_result):
            sha = current_sha(repo)

        assert sha == expected_sha


class TestRecordAndRetrieveSha:
    """Tests for SHA recording and retrieval."""

    def test_record_last_sha(self):
        """record_last_sha writes state file."""
        status = Status(result=ApplyResult.CHANGED, message="Applied successfully")

        mock_state_dir = MagicMock(spec=Path)
        mock_state_file = MagicMock(spec=Path)
        mock_state_dir.__truediv__ = lambda self, other: mock_state_file

        with patch("gitops_reconciler.wrapper.STATE_DIR", mock_state_dir):
            record_last_sha("my-target", "abc123", status)

            mock_state_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)
            written_data = mock_state_file.write_text.call_args[0][0]
            parsed = json.loads(written_data)
            assert parsed["sha"] == "abc123"
            assert parsed["result"] == "changed"
            assert parsed["message"] == "Applied successfully"

    def test_last_recorded_sha_exists(self):
        """last_recorded_sha returns SHA when file exists."""
        state_data = json.dumps({"sha": "def456", "result": "changed", "message": "OK"})

        mock_state_file = MagicMock(spec=Path)
        mock_state_file.exists.return_value = True
        mock_state_file.read_text.return_value = state_data

        with patch("gitops_reconciler.wrapper._state_file", return_value=mock_state_file):
            sha = last_recorded_sha("my-target")

        assert sha == "def456"

    def test_last_recorded_sha_not_exists(self):
        """last_recorded_sha returns None when file doesn't exist."""
        mock_state_file = MagicMock(spec=Path)
        mock_state_file.exists.return_value = False

        with patch("gitops_reconciler.wrapper._state_file", return_value=mock_state_file):
            sha = last_recorded_sha("my-target")

        assert sha is None


class TestDefaultNotifier:
    """Tests for the default notifier."""

    def test_default_notifier_logs_error(self):
        """default_notifier logs errors."""
        status = Status(result=ApplyResult.FAILED, message="Something broke")

        with patch("gitops_reconciler.wrapper.logger") as mock_logger:
            default_notifier("test-target", status)
            mock_logger.error.assert_called_once()


class TestTick:
    """Tests for the tick reconciliation function."""

    def test_tick_successful_apply_no_change(self):
        """tick with successful no-change apply."""
        status = Status(result=ApplyResult.NO_CHANGE)
        backend = MockBackend(status)
        target = ManagedTarget(name="test", backend=backend, repo=Path("/repo"))

        with patch("gitops_reconciler.wrapper.backend_lock") as mock_lock:
            mock_lock.return_value.__enter__ = Mock(return_value=True)
            mock_lock.return_value.__exit__ = Mock(return_value=False)

            with patch("gitops_reconciler.wrapper.sync_git"):
                with patch("gitops_reconciler.wrapper.current_sha", return_value="sha123"):
                    with patch("gitops_reconciler.wrapper.record_last_sha") as mock_record:
                        tick(target)

        assert backend.apply_called
        mock_record.assert_called_once_with("test", "sha123", status)

    def test_tick_successful_apply_changed(self):
        """tick with successful apply that made changes."""
        status = Status(result=ApplyResult.CHANGED, message="Resources updated")
        backend = MockBackend(status)
        target = ManagedTarget(name="test", backend=backend, repo=Path("/repo"))

        with patch("gitops_reconciler.wrapper.backend_lock") as mock_lock:
            mock_lock.return_value.__enter__ = Mock(return_value=True)
            mock_lock.return_value.__exit__ = Mock(return_value=False)

            with patch("gitops_reconciler.wrapper.sync_git"):
                with patch("gitops_reconciler.wrapper.current_sha", return_value="sha456"):
                    with patch("gitops_reconciler.wrapper.record_last_sha") as mock_record:
                        tick(target)

        assert backend.apply_called
        mock_record.assert_called_once_with("test", "sha456", status)

    def test_tick_failed_apply_does_not_record_sha(self):
        """tick with failed apply does not record SHA."""
        status = Status(result=ApplyResult.FAILED, message="Error occurred")
        backend = MockBackend(status)
        notifier = Mock()
        target = ManagedTarget(name="test", backend=backend, repo=Path("/repo"), notify=notifier)

        with patch("gitops_reconciler.wrapper.backend_lock") as mock_lock:
            mock_lock.return_value.__enter__ = Mock(return_value=True)
            mock_lock.return_value.__exit__ = Mock(return_value=False)

            with patch("gitops_reconciler.wrapper.sync_git"):
                with patch("gitops_reconciler.wrapper.record_last_sha") as mock_record:
                    tick(target)

        assert backend.apply_called
        mock_record.assert_not_called()
        notifier.assert_called_once_with("test", status)

    def test_tick_lock_not_acquired_skips(self):
        """tick skips when lock cannot be acquired."""
        backend = MockBackend(Status(result=ApplyResult.NO_CHANGE))
        target = ManagedTarget(name="test", backend=backend, repo=Path("/repo"))

        with patch("gitops_reconciler.wrapper.backend_lock") as mock_lock:
            mock_lock.return_value.__enter__ = Mock(return_value=False)
            mock_lock.return_value.__exit__ = Mock(return_value=False)

            tick(target)

        assert not backend.apply_called

    def test_tick_backend_exception_handled(self):
        """tick handles exceptions from backend.apply()."""

        class FailingBackend:
            def apply(self) -> Status:
                raise RuntimeError("Backend exploded")

            def destroy(self) -> Status:
                return Status(result=ApplyResult.FAILED)

            def get_outputs(self) -> dict[str, object]:
                return {}

        backend = FailingBackend()
        notifier = Mock()
        target = ManagedTarget(name="test", backend=backend, repo=Path("/repo"), notify=notifier)

        with patch("gitops_reconciler.wrapper.backend_lock") as mock_lock:
            mock_lock.return_value.__enter__ = Mock(return_value=True)
            mock_lock.return_value.__exit__ = Mock(return_value=False)

            with patch("gitops_reconciler.wrapper.sync_git"):
                tick(target)

        # Should have called the notifier with a FAILED status
        assert notifier.called
        call_args = notifier.call_args[0]
        assert call_args[0] == "test"
        assert call_args[1].result == ApplyResult.FAILED
        assert "RuntimeError" in call_args[1].message


class TestRun:
    """Tests for the run function (multiple targets)."""

    def test_run_multiple_targets(self):
        """run processes multiple targets sequentially."""
        backend1 = MockBackend(Status(result=ApplyResult.NO_CHANGE))
        backend2 = MockBackend(Status(result=ApplyResult.CHANGED))

        target1 = ManagedTarget(name="target1", backend=backend1, repo=Path("/repo1"))
        target2 = ManagedTarget(name="target2", backend=backend2, repo=Path("/repo2"))

        with patch("gitops_reconciler.wrapper.tick") as mock_tick:
            run([target1, target2])

        assert mock_tick.call_count == 2
        mock_tick.assert_any_call(target1)
        mock_tick.assert_any_call(target2)

    def test_run_empty_list(self):
        """run with empty target list does nothing."""
        with patch("gitops_reconciler.wrapper.tick") as mock_tick:
            run([])

        mock_tick.assert_not_called()


class TestManagedTarget:
    """Tests for ManagedTarget dataclass."""

    def test_managed_target_creation(self):
        """ManagedTarget can be created with required fields."""
        backend = MockBackend(Status(result=ApplyResult.NO_CHANGE))
        target = ManagedTarget(name="test", backend=backend, repo=Path("/repo"))

        assert target.name == "test"
        assert target.backend == backend
        assert target.repo == Path("/repo")
        assert target.notify == default_notifier

    def test_managed_target_custom_notifier(self):
        """ManagedTarget accepts custom notifier."""

        def custom_notifier(name: str, status: Status) -> None:
            pass

        backend = MockBackend(Status(result=ApplyResult.NO_CHANGE))
        target = ManagedTarget(
            name="test", backend=backend, repo=Path("/repo"), notify=custom_notifier
        )

        assert target.notify == custom_notifier

    def test_managed_target_is_frozen(self):
        """ManagedTarget is immutable."""
        from pydantic import ValidationError

        backend = MockBackend(Status(result=ApplyResult.NO_CHANGE))
        target = ManagedTarget(name="test", backend=backend, repo=Path("/repo"))

        with pytest.raises(ValidationError):  # pydantic frozen raises ValidationError
            target.name = "different"
