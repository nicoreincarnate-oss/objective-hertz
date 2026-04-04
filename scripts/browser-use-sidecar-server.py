#!/usr/bin/env python3
"""Local browser-use HTTP sidecar for ClawdBot browser_flow."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT_DIR / "tools" / "browser-use"
PID_FILE = ROOT_DIR / "logs" / "pids" / "browser-use.pid"


def _env_enabled(name: str, default: bool = True) -> bool:
	value = os.environ.get(name, "1" if default else "0").strip().lower()
	return value in {"1", "true", "yes", "on"}


def _base_url() -> str:
	return os.environ.get("BROWSER_USE_URL", "http://localhost:3031").strip().rstrip("/") or "http://localhost:3031"


def _port_from_url(value: str) -> int:
	parsed = urlparse(value)
	return parsed.port or 3031


def _session_name() -> str:
	return os.environ.get("CLAWDBOT_BROWSER_USE_SESSION", "clawdbot-browser").strip() or "clawdbot-browser"


def _browser_mode() -> str:
	mode = os.environ.get("CLAWDBOT_BROWSER_USE_BROWSER", "chromium").strip().lower()
	return mode if mode in {"chromium", "real", "remote"} else "chromium"


def _headed() -> bool:
	return _env_enabled("CLAWDBOT_BROWSER_USE_HEADED", False)


def _profile() -> str:
	return os.environ.get("CLAWDBOT_BROWSER_USE_PROFILE", "").strip()


def _llm_model() -> str:
	value = os.environ.get("CLAWDBOT_BROWSER_USE_LLM", os.environ.get("BROWSER_USE_LLM", "")).strip()
	if value:
		return value
	if os.environ.get("OPENAI_API_KEY", "").strip().lower() == "ollama" and os.environ.get("ANTHROPIC_API_KEY", "").strip():
		return "claude-sonnet-4-0"
	return ""


def _provider_family(model: str) -> str:
	value = model.strip().lower()
	if value.startswith(("claude", "anthropic")):
		return "anthropic"
	if value.startswith(("gpt", "o1", "o3", "o4")):
		return "openai"
	if value.startswith(("gemini", "google")):
		return "google"
	if value.startswith("bu-") or value.startswith("browser-use"):
		return "browser-use"
	return ""


def _cli_env() -> dict[str, str]:
	env = os.environ.copy()
	pythonpath_parts = [str(SOURCE_DIR), str(ROOT_DIR)]
	if env.get("PYTHONPATH"):
		pythonpath_parts.append(env["PYTHONPATH"])
	env["PYTHONPATH"] = ":".join(p for p in pythonpath_parts if p)
	env.setdefault("BROWSER_USE_LOGGING_LEVEL", "warning")
	env.setdefault("BROWSER_USE_SETUP_LOGGING", "false")
	family = _provider_family(_llm_model())
	if family == "anthropic":
		env.pop("OPENAI_API_KEY", None)
		env.pop("OPENAI_BASE_URL", None)
		env.pop("BROWSER_USE_API_KEY", None)
	elif family == "openai":
		env.pop("BROWSER_USE_API_KEY", None)
	elif family == "google":
		env.pop("OPENAI_API_KEY", None)
		env.pop("OPENAI_BASE_URL", None)
		env.pop("ANTHROPIC_API_KEY", None)
	return env


def _browser_use_cli(*args: str, timeout: float = 120.0) -> tuple[int, str, str]:
	command = [
		sys.executable,
		"-m",
		"browser_use.skill_cli.main",
		"--session",
		_session_name(),
		"--browser",
		_browser_mode(),
		"--json",
	]
	if _headed():
		command.append("--headed")
	if _profile():
		command.extend(["--profile", _profile()])
	if args and args[0] == "run" and _llm_model():
		command.extend(["run", "--llm", _llm_model(), *args[1:]])
	else:
		command.extend(args)

	proc = subprocess.run(
		command,
		env=_cli_env(),
		capture_output=True,
		text=True,
		timeout=timeout,
		check=False,
	)
	return proc.returncode, proc.stdout, proc.stderr


def _task_prompt(payload: dict) -> str:
	objective = str(payload.get("objective") or payload.get("description") or payload.get("task") or "").strip()
	url = str(payload.get("url") or "").strip()
	session_name = str(payload.get("session_name") or "").strip()
	profile_name = str(payload.get("profile_name") or "").strip()
	auth_context = payload.get("auth_context") or {}

	parts = [
		objective or "browser flow",
		"Use the live browser-use session server for this task.",
	]
	if url:
		parts.append(f"Target URL: {url}")
	if session_name:
		parts.append(f"Session name: {session_name}")
	if profile_name:
		parts.append(f"Profile name: {profile_name}")
	if auth_context:
		parts.append(f"Auth context: {json.dumps(auth_context, sort_keys=True, default=str)}")
	return "\n".join(parts)


def _ensure_browser_use_ready() -> None:
	rc, stdout, stderr = _browser_use_cli("state", timeout=90.0)
	if rc != 0:
		raise RuntimeError((stderr.strip() or stdout.strip() or "browser-use warmup failed")[:500])


class SidecarHandler(BaseHTTPRequestHandler):
	server_version = "browser-use-sidecar/1.0"

	def _write_json(self, status: int, payload: dict) -> None:
		body = json.dumps(payload, default=str).encode("utf-8")
		self.send_response(status)
		self.send_header("Content-Type", "application/json; charset=utf-8")
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def do_GET(self):  # noqa: N802
		if self.path.rstrip("/") == "/health":
			self._handle_health()
			return
		self._write_json(404, {"ok": False, "error": "not_found"})

	def do_POST(self):  # noqa: N802
		if self.path.rstrip("/") == "/run":
			self._handle_run()
			return
		self._write_json(404, {"ok": False, "error": "not_found"})

	def _handle_health(self) -> None:
		try:
			rc, stdout, stderr = _browser_use_cli("server", "status", timeout=10.0)
			payload = {
				"ok": rc == 0,
				"session": _session_name(),
				"browser": _browser_mode(),
				"headed": _headed(),
				"profile": _profile() or None,
				"llm": _llm_model() or None,
				"server_status": stdout.strip()[:500] if stdout.strip() else None,
			}
			if stderr.strip():
				payload["stderr"] = stderr.strip()[:500]
			self._write_json(200 if rc == 0 else 503, payload)
		except Exception as exc:
			self._write_json(
				503,
				{
					"ok": False,
					"session": _session_name(),
					"browser": _browser_mode(),
					"error": str(exc)[:500],
				},
			)

	def _handle_run(self) -> None:
		length = int(self.headers.get("Content-Length", "0") or "0")
		try:
			payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
		except json.JSONDecodeError as exc:
			self._write_json(400, {"ok": False, "error": f"invalid_json: {exc}"})
			return

		task = _task_prompt(payload)
		try:
			rc, stdout, stderr = _browser_use_cli("run", task, timeout=900.0)
		except subprocess.TimeoutExpired:
			self._write_json(504, {"ok": False, "error": "browser-use timed out"})
			return
		except Exception as exc:
			self._write_json(500, {"ok": False, "error": str(exc)[:500]})
			return

		if rc != 0:
			self._write_json(
				502,
				{
					"ok": False,
					"error": (stderr.strip() or stdout.strip() or f"browser-use exited with {rc}")[:500],
				},
			)
			return

		output = stdout.strip()
		if not output:
			self._write_json(200, {"ok": True, "status": "completed", "executor": "browser_use", "output": ""})
			return

		try:
			parsed = json.loads(output)
		except json.JSONDecodeError:
			self._write_json(
				200,
				{
					"ok": True,
					"status": "completed",
					"executor": "browser_use",
					"output": output[:4000],
				},
			)
			return

		if isinstance(parsed, dict):
			parsed.setdefault("ok", True)
			parsed.setdefault("status", "completed")
			parsed.setdefault("executor", "browser_use")
			self._write_json(200, parsed)
			return

		self._write_json(
			200,
			{
				"ok": True,
				"status": "completed",
				"executor": "browser_use",
				"output": output[:4000],
			},
		)

	def log_message(self, format: str, *args) -> None:  # noqa: A003
		return


def main() -> None:
	_ensure_browser_use_ready()
	PID_FILE.parent.mkdir(parents=True, exist_ok=True)
	PID_FILE.write_text(str(os.getpid()))
	server = ThreadingHTTPServer(("127.0.0.1", _port_from_url(_base_url())), SidecarHandler)
	try:
		server.serve_forever()
	except KeyboardInterrupt:
		pass
	finally:
		try:
			PID_FILE.unlink()
		except FileNotFoundError:
			pass
		server.server_close()


if __name__ == "__main__":
	main()
