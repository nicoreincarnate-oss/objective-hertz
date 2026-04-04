"""Bridge to the real upstream ByteDance DeerFlow engine."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DeerFlowEngineResult:
    ok: bool
    engine: str
    analysis_markdown: str
    metadata: dict[str, Any]


class DeerFlowEmbeddedEngine:
    """Run the upstream DeerFlow embedded client in the project Python 3.12 env."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self._root_dir = root_dir or Path(__file__).resolve().parent.parent
        self._python_path = self._root_dir / ".venv312" / "bin" / "python"
        self._runner_path = self._root_dir / "scripts" / "deerflow_embedded_runner.py"
        self._config_path = self._root_dir / "tools" / "deer-flow" / "config.yaml"
        self._harness_path = self._root_dir / "tools" / "deer-flow" / "backend" / "packages" / "harness"

    def is_available(self) -> bool:
        return all(
            path.exists()
            for path in (
                self._python_path,
                self._runner_path,
                self._config_path,
                self._harness_path,
            )
        )

    async def health_check(self) -> dict[str, Any]:
        return await self._invoke({"action": "health"})

    async def analyze(
        self,
        *,
        mode: str,
        topic: str,
        sources: list[str],
        objectives: list[str],
        items: list[dict[str, Any]],
        lookback_hours: int | None = None,
    ) -> DeerFlowEngineResult:
        payload = {
            "action": "analyze",
            "mode": mode,
            "topic": topic,
            "sources": sources,
            "objectives": objectives,
            "items": items,
        }
        if lookback_hours is not None:
            payload["lookback_hours"] = lookback_hours
        response = await self._invoke(payload)
        return DeerFlowEngineResult(
            ok=bool(response.get("ok")),
            engine=str(response.get("engine") or "deerflow_client"),
            analysis_markdown=str(response.get("analysis_markdown") or "").strip(),
            metadata={
                **dict(response.get("metadata") or {}),
                **({"error": response.get("error")} if response.get("error") else {}),
            },
        )

    async def _invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_available():
            return {
                "ok": False,
                "engine": "deerflow_client",
                "error": "embedded DeerFlow runtime is unavailable",
                "metadata": {
                    "python_path": str(self._python_path),
                    "runner_path": str(self._runner_path),
                    "config_path": str(self._config_path),
                },
            }

        process = await asyncio.create_subprocess_exec(
            str(self._python_path),
            str(self._runner_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._root_dir),
        )
        stdout, stderr = await process.communicate(json.dumps(payload).encode("utf-8"))
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        stdout_text = stdout.decode("utf-8", errors="replace").strip()

        if process.returncode != 0:
            return {
                "ok": False,
                "engine": "deerflow_client",
                "error": stderr_text or stdout_text or f"runner exited with code {process.returncode}",
                "metadata": {
                    "returncode": process.returncode,
                },
            }

        try:
            data = json.loads(stdout_text)
        except json.JSONDecodeError:
            return {
                "ok": False,
                "engine": "deerflow_client",
                "error": "runner returned invalid JSON",
                "metadata": {
                    "stdout": stdout_text[:1000],
                    "stderr": stderr_text[:1000],
                },
            }

        if stderr_text:
            data.setdefault("metadata", {})
            data["metadata"]["stderr"] = stderr_text[:1000]
        return data
