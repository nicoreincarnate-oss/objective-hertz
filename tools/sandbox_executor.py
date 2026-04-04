"""Sandboxed code execution via OJ sandbox.

Runs untrusted code in isolated Docker containers with resource limits.
Used by ClawdBot for testing generated sites and executing user scripts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import guard: try OJ sandbox, fall back to subprocess
# ---------------------------------------------------------------------------

_HAS_OJ_SANDBOX = False
_ContainerRunner = None

try:
    from openjarvis.sandbox.runner import ContainerRunner as _ContainerRunner  # type: ignore[assignment]
    from openjarvis.sandbox.mount_security import MountAllowlist, validate_mounts  # noqa: F401

    _HAS_OJ_SANDBOX = True
except ImportError:
    logger.warning(
        "openjarvis.sandbox not available; sandbox_executor will use "
        "subprocess fallback (less isolation)."
    )

# ---------------------------------------------------------------------------
# Language config
# ---------------------------------------------------------------------------

_LANGUAGE_IMAGES: dict[str, str] = {
    "python": "python:3.12-slim",
    "node": "node:20-slim",
    "javascript": "node:20-slim",
    "bash": "bash:5",
    "sh": "bash:5",
    "ruby": "ruby:3.3-slim",
}

_EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "javascript",
    ".rb": "ruby",
    ".sh": "bash",
    ".bash": "bash",
}

_LANGUAGE_COMMANDS: dict[str, list[str]] = {
    "python": ["python", "-c"],
    "javascript": ["node", "-e"],
    "node": ["node", "-e"],
    "bash": ["bash", "-c"],
    "sh": ["sh", "-c"],
    "ruby": ["ruby", "-e"],
}


def _detect_language(file_path: str) -> str:
    """Detect language from file extension."""
    ext = Path(file_path).suffix.lower()
    return _EXTENSION_MAP.get(ext, "python")


def _detect_runtime() -> str | None:
    """Return 'docker' or 'podman' if available, else None."""
    for runtime in ("docker", "podman"):
        if shutil.which(runtime):
            return runtime
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def sandbox_available() -> bool:
    """Check if Docker/Podman is available for sandboxed execution."""
    loop = asyncio.get_event_loop()
    runtime = await loop.run_in_executor(None, _detect_runtime)
    return runtime is not None


async def execute_code(
    code: str,
    language: str = "python",
    timeout: int = 30,
    memory_limit: str = "256m",
    cpu_limit: float = 0.5,
    mount_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Execute code in a sandboxed container.

    Falls back to subprocess if Docker/Podman is unavailable.

    Returns
    -------
    dict
        ``success``, ``stdout``, ``stderr``, ``exit_code``,
        ``execution_time``, and optionally ``error``.
    """
    loop = asyncio.get_event_loop()
    runtime = await loop.run_in_executor(None, _detect_runtime)

    if runtime:
        return await loop.run_in_executor(
            None,
            _execute_in_container,
            code,
            language,
            timeout,
            memory_limit,
            cpu_limit,
            mount_paths,
            runtime,
        )
    else:
        logger.warning(
            "Docker/Podman not available; returning error result."
        )
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "exit_code": -1,
            "execution_time": 0.0,
            "error": (
                "No container runtime available. "
                "Install Docker or Podman for sandboxed execution."
            ),
        }


async def execute_file(
    file_path: str,
    language: str | None = None,
    timeout: int = 30,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute a file in sandbox.

    If *language* is ``None``, it is auto-detected from the file extension.
    """
    resolved = Path(file_path).resolve()
    if not resolved.is_file():
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "exit_code": -1,
            "execution_time": 0.0,
            "error": f"File not found: {file_path}",
        }

    lang = language or _detect_language(file_path)
    code = resolved.read_text(encoding="utf-8")

    return await execute_code(
        code=code,
        language=lang,
        timeout=timeout,
        **kwargs,
    )


async def verify_site(
    site_dir: str,
    port: int = 8080,
    timeout: int = 10,
) -> dict[str, Any]:
    """Start a simple HTTP server in sandbox and verify site loads.

    Serves ``site_dir`` on *port* inside a container, then curls
    localhost to verify the index page loads successfully.

    Returns
    -------
    dict
        ``success``, ``server_started``, ``index_loads``, ``errors``.
    """
    resolved = Path(site_dir).resolve()
    errors: list[str] = []

    if not resolved.is_dir():
        return {
            "success": False,
            "server_started": False,
            "index_loads": False,
            "errors": [f"Directory not found: {site_dir}"],
        }

    # Check for an index file
    has_index = any(
        (resolved / name).is_file()
        for name in ("index.html", "index.htm", "index.php")
    )
    if not has_index:
        errors.append("No index.html found in site directory.")

    loop = asyncio.get_event_loop()
    runtime = await loop.run_in_executor(None, _detect_runtime)

    if not runtime:
        return {
            "success": False,
            "server_started": False,
            "index_loads": False,
            "errors": ["No container runtime (Docker/Podman) available."],
        }

    return await loop.run_in_executor(
        None,
        _test_site_in_container,
        str(resolved),
        port,
        timeout,
        runtime,
        errors,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _execute_in_container(
    code: str,
    language: str,
    timeout: int,
    memory_limit: str,
    cpu_limit: float,
    mount_paths: list[str] | None,
    runtime: str,
) -> dict[str, Any]:
    """Run code inside a Docker/Podman container."""
    container_name = f"oj-exec-{uuid.uuid4().hex[:12]}"
    image = _LANGUAGE_IMAGES.get(language, "python:3.12-slim")
    cmd_prefix = _LANGUAGE_COMMANDS.get(language, ["python", "-c"])

    args: list[str] = [
        runtime,
        "run",
        "--rm",
        "--name", container_name,
        "--label", "openjarvis-sandbox=true",
        "--network", "none",
        f"--memory={memory_limit}",
        f"--cpus={cpu_limit}",
    ]

    # Mount paths (read-only)
    if mount_paths:
        if _HAS_OJ_SANDBOX:
            try:
                allowlist = MountAllowlist()
                mount_paths = validate_mounts(mount_paths, allowlist)
            except ValueError as exc:
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": str(exc),
                    "exit_code": -1,
                    "execution_time": 0.0,
                    "error": f"Mount validation failed: {exc}",
                }

        for mp in mount_paths:
            args.extend(["-v", f"{mp}:{mp}:ro"])

    args.extend([image, *cmd_prefix, code])

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = time.monotonic() - t0

        logger.info(
            "Sandbox execution completed: language=%s exit=%d time=%.2fs",
            language,
            proc.returncode,
            elapsed,
        )

        return {
            "success": proc.returncode == 0,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "execution_time": round(elapsed, 3),
        }

    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        _force_remove_container(runtime, container_name)
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "exit_code": -1,
            "execution_time": round(elapsed, 3),
            "error": f"Execution timed out after {timeout}s.",
        }
    except Exception as exc:
        elapsed = time.monotonic() - t0
        _force_remove_container(runtime, container_name)
        return {
            "success": False,
            "stdout": "",
            "stderr": str(exc),
            "exit_code": -1,
            "execution_time": round(elapsed, 3),
            "error": f"Container execution error: {exc}",
        }


def _test_site_in_container(
    site_dir: str,
    port: int,
    timeout: int,
    runtime: str,
    errors: list[str],
) -> dict[str, Any]:
    """Serve a site directory inside a container and verify it loads."""
    container_name = f"oj-site-{uuid.uuid4().hex[:12]}"

    # Use python HTTP server inside the container
    serve_cmd = (
        f"cd /site && python -m http.server {port} &"
        f" sleep 1 && "
        f"curl -sf http://localhost:{port}/ > /dev/null 2>&1"
    )

    args: list[str] = [
        runtime,
        "run",
        "--rm",
        "--name", container_name,
        "--label", "openjarvis-sandbox=true",
        "--network", "none",
        "--memory=128m",
        "--cpus=0.5",
        "-v", f"{site_dir}:/site:ro",
        "python:3.12-slim",
        "bash", "-c", serve_cmd,
    ]

    t0 = time.monotonic()
    server_started = False
    index_loads = False

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = time.monotonic() - t0

        # If curl exited 0, the server started and index loaded
        server_started = True
        index_loads = proc.returncode == 0

        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            if stderr:
                errors.append(stderr)
            errors.append(f"Site test failed with exit code {proc.returncode}.")

        logger.info(
            "Site test completed: dir=%s started=%s loads=%s time=%.2fs",
            site_dir,
            server_started,
            index_loads,
            elapsed,
        )

    except subprocess.TimeoutExpired:
        _force_remove_container(runtime, container_name)
        errors.append(f"Site test timed out after {timeout}s.")

    except Exception as exc:
        _force_remove_container(runtime, container_name)
        errors.append(f"Site test error: {exc}")

    return {
        "success": index_loads and not errors,
        "server_started": server_started,
        "index_loads": index_loads,
        "errors": errors,
    }


def _force_remove_container(runtime: str, container_name: str) -> None:
    """Force-remove a container, ignoring errors."""
    try:
        subprocess.run(
            [runtime, "rm", "-f", container_name],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        logger.debug(
            "Failed to remove container %s", container_name, exc_info=True,
        )


__all__ = [
    "execute_code",
    "execute_file",
    "sandbox_available",
    "verify_site",
]
