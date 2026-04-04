"""Tests for tools.sandbox_executor -- sandboxed code execution wrapper."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.sandbox_executor import (
    _detect_language,
    _force_remove_container,
    execute_code,
    execute_file,
    sandbox_available,
    verify_site,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_loop():
    """Create a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _run(coro, loop=None):
    """Run an async function synchronously."""
    if loop is None:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# 1. test_sandbox_available
# ---------------------------------------------------------------------------


class TestSandboxAvailable:
    """Mock Docker/Podman check."""

    @patch("tools.sandbox_executor.shutil.which", return_value="/usr/bin/docker")
    def test_available_when_docker_present(self, mock_which):
        assert _run(sandbox_available()) is True

    @patch("tools.sandbox_executor.shutil.which", return_value=None)
    def test_unavailable_when_no_runtime(self, mock_which):
        assert _run(sandbox_available()) is False


# ---------------------------------------------------------------------------
# 2. test_execute_code_success
# ---------------------------------------------------------------------------


class TestExecuteCodeSuccess:
    """Mock container execution returning success."""

    @patch("tools.sandbox_executor._detect_runtime", return_value="docker")
    @patch("tools.sandbox_executor.subprocess.run")
    def test_successful_python_execution(self, mock_run, mock_runtime):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="hello world\n",
            stderr="",
        )

        result = _run(execute_code("print('hello world')", language="python"))

        assert result["success"] is True
        assert result["stdout"] == "hello world\n"
        assert result["exit_code"] == 0
        assert "execution_time" in result

        # Verify container args include resource limits
        call_args = mock_run.call_args[0][0]
        assert "--memory=256m" in call_args
        assert "--cpus=0.5" in call_args
        assert "--network" in call_args


# ---------------------------------------------------------------------------
# 3. test_execute_code_timeout
# ---------------------------------------------------------------------------


class TestExecuteCodeTimeout:
    """Timeout produces error result and triggers cleanup."""

    @patch("tools.sandbox_executor._detect_runtime", return_value="docker")
    @patch("tools.sandbox_executor._force_remove_container")
    @patch(
        "tools.sandbox_executor.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=5),
    )
    def test_timeout_returns_error(self, mock_run, mock_cleanup, mock_runtime):
        result = _run(execute_code("import time; time.sleep(999)", timeout=5))

        assert result["success"] is False
        assert "timed out" in result["error"]
        assert result["exit_code"] == -1
        mock_cleanup.assert_called_once()


# ---------------------------------------------------------------------------
# 4. test_execute_code_no_docker
# ---------------------------------------------------------------------------


class TestExecuteCodeNoDocker:
    """Graceful error when no container runtime is available."""

    @patch("tools.sandbox_executor._detect_runtime", return_value=None)
    def test_no_docker_returns_error_without_crash(self, mock_runtime):
        result = _run(execute_code("print('hi')"))

        assert result["success"] is False
        assert "No container runtime" in result["error"]
        assert result["exit_code"] == -1
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 5. test_execute_file_auto_detect_language
# ---------------------------------------------------------------------------


class TestExecuteFileAutoDetect:
    """Language detection from file extension."""

    def test_detect_python(self):
        assert _detect_language("script.py") == "python"

    def test_detect_javascript(self):
        assert _detect_language("app.js") == "javascript"

    def test_detect_bash(self):
        assert _detect_language("run.sh") == "bash"

    def test_detect_ruby(self):
        assert _detect_language("main.rb") == "ruby"

    def test_unknown_defaults_to_python(self):
        assert _detect_language("data.xyz") == "python"

    @patch("tools.sandbox_executor._detect_runtime", return_value="docker")
    @patch("tools.sandbox_executor.subprocess.run")
    def test_execute_file_reads_and_delegates(self, mock_run, mock_runtime, tmp_path):
        script = tmp_path / "hello.py"
        script.write_text("print('hello from file')")

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="hello from file\n",
            stderr="",
        )

        result = _run(execute_file(str(script)))

        assert result["success"] is True
        assert result["stdout"] == "hello from file\n"

        # Verify the code content was passed to the container
        call_args = mock_run.call_args[0][0]
        assert "print('hello from file')" in call_args
        # Verify python image was selected (auto-detected from .py)
        assert "python:3.12-slim" in call_args

    def test_execute_file_not_found(self):
        result = _run(execute_file("/nonexistent/path/script.py"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()


# ---------------------------------------------------------------------------
# 6. test_test_site_success
# ---------------------------------------------------------------------------


class TestTestSiteSuccess:
    """Site testing with mocked container."""

    @patch("tools.sandbox_executor._detect_runtime", return_value="docker")
    @patch("tools.sandbox_executor.subprocess.run")
    def test_site_loads_successfully(self, mock_run, mock_runtime, tmp_path):
        index = tmp_path / "index.html"
        index.write_text("<html><body>Hello</body></html>")

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )

        result = _run(verify_site(str(tmp_path)))

        assert result["success"] is True
        assert result["server_started"] is True
        assert result["index_loads"] is True
        assert result["errors"] == []

    @patch("tools.sandbox_executor._detect_runtime", return_value=None)
    def test_site_no_docker(self, mock_runtime, tmp_path):
        index = tmp_path / "index.html"
        index.write_text("<html></html>")

        result = _run(verify_site(str(tmp_path)))

        assert result["success"] is False
        assert result["server_started"] is False
        assert any("container runtime" in e.lower() for e in result["errors"])

    def test_site_dir_not_found(self):
        result = _run(verify_site("/nonexistent/site/dir"))

        assert result["success"] is False
        assert any("not found" in e.lower() for e in result["errors"])


# ---------------------------------------------------------------------------
# 7. test_resource_limits_applied
# ---------------------------------------------------------------------------


class TestResourceLimitsApplied:
    """Custom resource limits are forwarded to Docker args."""

    @patch("tools.sandbox_executor._detect_runtime", return_value="docker")
    @patch("tools.sandbox_executor.subprocess.run")
    def test_custom_limits_in_docker_args(self, mock_run, mock_runtime):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        _run(
            execute_code(
                "x = 1",
                memory_limit="512m",
                cpu_limit=1.0,
                timeout=60,
            )
        )

        call_args = mock_run.call_args[0][0]
        assert "--memory=512m" in call_args
        assert "--cpus=1.0" in call_args
        assert mock_run.call_args[1]["timeout"] == 60


# ---------------------------------------------------------------------------
# 8. test_cleanup_after_execution
# ---------------------------------------------------------------------------


class TestCleanupAfterExecution:
    """Container cleanup behaviour."""

    @patch("tools.sandbox_executor.subprocess.run")
    def test_force_remove_called(self, mock_run):
        _force_remove_container("docker", "oj-exec-abc123")

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args == ["docker", "rm", "-f", "oj-exec-abc123"]

    @patch("tools.sandbox_executor.subprocess.run", side_effect=Exception("boom"))
    def test_cleanup_never_raises(self, mock_run):
        # Must not raise even if subprocess fails
        _force_remove_container("docker", "oj-exec-fail")

    @patch("tools.sandbox_executor._detect_runtime", return_value="docker")
    @patch("tools.sandbox_executor._force_remove_container")
    @patch(
        "tools.sandbox_executor.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=5),
    )
    def test_cleanup_on_timeout(self, mock_run, mock_cleanup, mock_runtime):
        _run(execute_code("while True: pass", timeout=5))
        mock_cleanup.assert_called_once()
