"""P0-3: Subprocess wrapper for macOS sandbox-exec verifier runs.

Wraps test execution in the sandbox profile at litellm/sandboxes/verifier.sb.
Ensures untrusted LLM-generated patches can't escape to touch ~/.ssh, wallets,
or env files.

Threat model addressed: prompt-injected Ruflo fix that tries to run
`rm -rf ~/.ssh` or exfiltrate Conway wallets. Sandbox denies network egress
and home-dir reads outside the scratch worktree.

CRITICAL: callers MUST pass -D HOME=$HOME when invoking sandbox-exec or the
(param "HOME") substitutions in the profile silently no-op. This runner
handles that automatically.

Per PORT-PLAN §2 P0-3 fix — wired into ruflo_loop.py as the default sandbox_runner.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_PROFILE = _REPO_ROOT / "litellm" / "sandboxes" / "verifier.sb"


class SandboxRunner:
    """Run subprocesses inside macOS sandbox-exec with the verifier profile."""

    def __init__(self, profile_path: Path | None = None):
        self.profile = profile_path or _DEFAULT_PROFILE
        if not self.profile.exists():
            raise FileNotFoundError(
                f"Sandbox profile not found: {self.profile}. "
                "P0-3 sandbox runner requires litellm/sandboxes/verifier.sb."
            )
        if shutil.which("sandbox-exec") is None:
            raise RuntimeError(
                "sandbox-exec not found in PATH. P0-3 sandbox runner requires macOS."
            )
        home = os.environ.get("HOME")
        if not home:
            raise RuntimeError(
                "HOME env var not set. P0-3 sandbox runner requires HOME to resolve "
                '(param "HOME") substitutions in the profile.'
            )
        self._home = home

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout_s: int = 60,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        """Execute a command inside the sandbox.

        Passes -D HOME=$HOME so the profile's (param "HOME") resolves correctly.
        Without this the deny rules silently no-op.
        """
        full_cmd = [
            "sandbox-exec",
            "-D", f"HOME={self._home}",
            "-f", str(self.profile),
            *command,
        ]
        return subprocess.run(
            full_cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )

    def run_pytest(
        self,
        test_path: Path,
        *,
        cwd: Path | None = None,
        timeout_s: int = 120,
    ) -> subprocess.CompletedProcess:
        """Convenience: run pytest on a specific path inside the sandbox."""
        return self.run(
            ["python3", "-m", "pytest", str(test_path), "-q", "--tb=short"],
            cwd=cwd,
            timeout_s=timeout_s,
        )
