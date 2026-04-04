"""Backend adapters for system executor actions.

The daemon prefers donor backends when they are present, but always has
a local fallback so the surface remains functional on a plain macOS host.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from abc import ABC
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("perseus.system_executor.adapters")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _which(*names: str) -> str:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return ""


def _safe_text(value: Any) -> str:
    return "" if value is None else str(value)


def _apple_script_modifier(name: str) -> str:
    mapping = {
        "cmd": "command",
        "command": "command",
        "ctrl": "control",
        "control": "control",
        "alt": "option",
        "option": "option",
        "shift": "shift",
        "fn": "fn",
        "function": "fn",
    }
    return mapping.get(name.lower(), name.lower())


def _run_subprocess(args: list[str], *, timeout: float = 30.0, cwd: str = "", shell: bool = False) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args if not shell else " ".join(args),
            shell=shell,
            cwd=cwd or None,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
        }
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "timeout": True, "stderr": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _run_osascript(script: str, *, timeout: float = 20.0) -> dict[str, Any]:
    binary = _which("osascript")
    if not binary:
        return {"ok": False, "error": "osascript is unavailable"}
    proc = subprocess.run(
        [binary, "-e", script],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


class BackendUnavailable(RuntimeError):
    """Raised when an adapter cannot handle a capability."""


class SystemBackendAdapter(ABC):
    """Base class for donor backends and local fallbacks."""

    name = "base"

    def available(self) -> bool:
        return True

    def supports(self, capability: str) -> bool:
        return True

    def ready(self) -> bool:
        """Return True when the backend is healthy enough for live traffic."""
        return self.available()

    def health(self) -> dict[str, Any]:
        return {"name": self.name, "available": self.available()}

    def desktop_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise BackendUnavailable(f"{self.name} does not implement desktop_control")

    def desktop_exec(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise BackendUnavailable(f"{self.name} does not implement desktop_exec")

    def system_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise BackendUnavailable(f"{self.name} does not implement system_action")

    def screen_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise BackendUnavailable(f"{self.name} does not implement screen_context")


class RemoteJSONAdapter(SystemBackendAdapter):
    """Tiny HTTP adapter for donor backends that expose JSON endpoints."""

    def __init__(self, name: str, base_url: str):
        self.name = name
        self.base_url = base_url.rstrip("/")

    def available(self) -> bool:
        return bool(self.base_url)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        resp = httpx.post(f"{self.base_url}{path}", json=payload, timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"result": data}

    def _get(self, path: str, params: dict[str, Any] | None = None, *, timeout: float = 20.0) -> dict[str, Any]:
        import httpx

        resp = httpx.get(f"{self.base_url}{path}", params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"result": data}

    def health(self) -> dict[str, Any]:
        if not self.available():
            return {"name": self.name, "available": False, "status": "unconfigured"}
        import httpx

        last_error = ""
        for path in ("/health", "/a2a/health"):
            try:
                resp = httpx.get(f"{self.base_url}{path}", timeout=5.0)
                if resp.status_code == 200:
                    return {"name": self.name, "available": True, "status": "ok", "endpoint": path}
            except Exception as exc:
                last_error = str(exc)
        return {"name": self.name, "available": True, "status": "degraded", "error": last_error[:200]}

    def ready(self) -> bool:
        return self.health().get("status") == "ok"

    def desktop_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/desktop_control", payload)

    def desktop_exec(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/desktop_exec", payload)

    def system_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/system_action", payload)

    def screen_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/screen_context", payload)


class PeekabooAdapter(RemoteJSONAdapter):
    name = "peekaboo"

    def __init__(self) -> None:
        super().__init__(self.name, _env("SYSTEM_EXECUTOR_PEEKABOO_URL") or _env("PEEKABOO_URL"))
        self.binary = _which("peekaboo")

    def supports(self, capability: str) -> bool:
        return capability in {"desktop_control", "system_action", "screen_context", "health_check"}

    def available(self) -> bool:
        return bool(self.base_url or self.binary)

    def _use_remote(self) -> bool:
        return bool(self.base_url)

    def _run_cli(self, *args: str, timeout: float = 20.0) -> dict[str, Any]:
        if not self.binary:
            raise BackendUnavailable("peekaboo CLI is unavailable")
        return _run_subprocess([self.binary, *args, "--json", "--no-remote"], timeout=timeout)

    def _parse_cli_response(self, result: dict[str, Any], *, action: str) -> dict[str, Any]:
        stdout = _safe_text(result.get("stdout", "")).strip()
        if not stdout:
            return {"backend": self.name, "action": action, **result}
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            return {"backend": self.name, "action": action, **result}

        if isinstance(parsed, dict):
            payload = parsed.get("data")
            response = payload if isinstance(payload, dict) else {"result": payload}
            response["backend"] = self.name
            response["action"] = action
            response["ok"] = bool(parsed.get("success", result.get("ok", False)))
            if parsed.get("error"):
                response["error"] = parsed["error"]
            if result.get("stderr"):
                response["stderr"] = result["stderr"]
            return response

        return {"backend": self.name, "action": action, "ok": bool(result.get("ok")), "result": parsed}

    def health(self) -> dict[str, Any]:
        if self._use_remote():
            return super().health()
        if not self.binary:
            return {"name": self.name, "available": False, "status": "unconfigured"}

        result = self._run_cli("permissions", timeout=10.0)
        parsed = self._parse_cli_response(result, action="permissions")
        permissions = parsed.get("permissions", [])
        missing_permissions = [
            permission.get("name", "")
            for permission in permissions
            if isinstance(permission, dict) and permission.get("isRequired") and not permission.get("isGranted")
        ]
        status = "ok" if not missing_permissions and parsed.get("ok") else "degraded"
        return {
            "name": self.name,
            "available": True,
            "status": status,
            "mode": "cli",
            "permissions": permissions,
            "missing_permissions": missing_permissions,
            "binary": self.binary,
        }

    def ready(self) -> bool:
        return self.health().get("status") == "ok"

    def desktop_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._use_remote():
            return super().desktop_control(payload)

        action = _safe_text(payload.get("action", ""))
        point = payload.get("point") or {}
        x = int(point.get("x", payload.get("x", 0)) or 0)
        y = int(point.get("y", payload.get("y", 0)) or 0)
        text = _safe_text(payload.get("text", payload.get("value", "")))
        key = _safe_text(payload.get("key", text))
        modifiers = [str(item) for item in payload.get("modifiers", []) if item]

        if action in {"click", "double_click"}:
            args = ["click", "--coords", f"{x},{y}"]
            if action == "double_click":
                args.append("--double")
            return self._parse_cli_response(self._run_cli(*args), action=action)
        if action in {"type", "type_text"}:
            return self._parse_cli_response(self._run_cli("type", text), action=action)
        if action in {"press", "press_key", "hotkey"}:
            if modifiers:
                combo = ",".join([*modifiers, key])
                return self._parse_cli_response(self._run_cli("hotkey", "--keys", combo), action=action)
            return self._parse_cli_response(self._run_cli("press", key), action=action)
        raise BackendUnavailable(f"peekaboo cannot handle desktop_control action '{action}'")

    def system_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._use_remote():
            return super().system_action(payload)

        action = _safe_text(payload.get("action", ""))
        app = _safe_text(payload.get("app", payload.get("target", "")))
        url = _safe_text(payload.get("url", ""))
        path = _safe_text(payload.get("path", ""))

        if action == "open_app" and app:
            return self._parse_cli_response(self._run_cli("app", "launch", app), action=action)
        if action == "focus_app" and app:
            return self._parse_cli_response(self._run_cli("app", "switch", "--to", app), action=action)
        if action == "open_url" and url:
            return self._parse_cli_response(self._run_cli("open", url), action=action)
        if action == "open_path" and path:
            return self._parse_cli_response(self._run_cli("open", path), action=action)
        raise BackendUnavailable(f"peekaboo cannot handle system_action '{action}'")

    def screen_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._use_remote():
            return super().screen_context(payload)

        args = ["see", "--mode", "frontmost"]
        app = _safe_text(payload.get("app_name", payload.get("app", "")))
        window_title = _safe_text(payload.get("window_title", ""))
        window_id = _safe_text(payload.get("window_id", ""))
        if app:
            args.extend(["--app", app])
        if window_title:
            args.extend(["--window-title", window_title])
        if window_id:
            args.extend(["--window-id", window_id])
        return self._parse_cli_response(self._run_cli(*args, timeout=30.0), action="screen_context")


class CuaAdapter(RemoteJSONAdapter):
    name = "cua"

    def __init__(self) -> None:
        super().__init__(self.name, _env("SYSTEM_EXECUTOR_CUA_URL") or _env("CUA_URL"))


class ScreenpipeAdapter(RemoteJSONAdapter):
    name = "screenpipe"

    def __init__(self) -> None:
        super().__init__(self.name, _env("SYSTEM_EXECUTOR_SCREENPIPE_URL") or _env("SCREENPIPE_URL") or "http://localhost:3030")

    def supports(self, capability: str) -> bool:
        return capability in {"screen_context", "health_check"}

    def health(self) -> dict[str, Any]:
        if not self.available():
            return {"name": self.name, "available": False, "status": "unconfigured"}
        try:
            data = self._get("/health", timeout=5.0)
            if isinstance(data, dict):
                return {"name": self.name, "available": True, "status": "ok", "endpoint": "/health", **data}
        except Exception as exc:
            return {"name": self.name, "available": True, "status": "degraded", "error": str(exc)[:200]}
        return {"name": self.name, "available": True, "status": "ok", "endpoint": "/health"}

    def _build_search_params(self, payload: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": int(payload.get("limit", _env("SCREENPIPE_SCREEN_CONTEXT_LIMIT", "1") or 1) or 1),
            "include_frames": str(payload.get("include_frames", _env("SCREENPIPE_SCREEN_CONTEXT_INCLUDE_FRAMES", "true") or "true")).lower() not in {"0", "false", "no"},
        }
        query = _safe_text(payload.get("query", payload.get("q", ""))).strip()
        if query:
            params["q"] = query

        content_type = _safe_text(payload.get("content_type", _env("SCREENPIPE_SCREEN_CONTEXT_CONTENT_TYPE", ""))).strip()
        if content_type:
            params["content_type"] = content_type

        app_name = _safe_text(payload.get("app_name", "")).strip()
        if app_name:
            params["app_name"] = app_name

        window_name = _safe_text(payload.get("window_name", "")).strip()
        if window_name:
            params["window_name"] = window_name

        browser_url = _safe_text(payload.get("browser_url", "")).strip()
        if browser_url:
            params["browser_url"] = browser_url

        minutes = payload.get("minutes")
        if minutes not in (None, ""):
            try:
                minutes_value = max(int(minutes), 0)
                params["start_time"] = (datetime.now(timezone.utc) - timedelta(minutes=minutes_value)).isoformat()
                params["end_time"] = datetime.now(timezone.utc).isoformat()
            except Exception:
                pass

        start_time = _safe_text(payload.get("start_time", "")).strip()
        if start_time:
            params["start_time"] = start_time

        end_time = _safe_text(payload.get("end_time", "")).strip()
        if end_time:
            params["end_time"] = end_time

        return params

    def _summarize_result(self, item: dict[str, Any]) -> dict[str, Any]:
        content = item.get("content", {}) if isinstance(item.get("content", {}), dict) else {}
        summary = {
            "type": item.get("type", ""),
            "timestamp": content.get("timestamp", ""),
            "text": content.get("text") or content.get("transcription") or content.get("title") or "",
            "app_name": content.get("app_name") or content.get("appName") or "",
            "window_name": content.get("window_name") or content.get("windowName") or "",
            "browser_url": content.get("browser_url") or content.get("browserUrl") or "",
            "focused": content.get("focused"),
            "frame_id": content.get("frame_id") or content.get("frameId"),
            "file_path": content.get("file_path") or content.get("filePath") or "",
        }
        frame = content.get("frame")
        if isinstance(frame, str) and frame:
            summary["frame"] = frame
        elif isinstance(frame, dict):
            summary["frame"] = frame
        return {key: value for key, value in summary.items() if value not in ("", None, [])}

    def screen_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = self._build_search_params(payload)
        data = self._get("/search", params=params, timeout=15.0)
        results = data.get("data", []) if isinstance(data, dict) else []
        if not isinstance(results, list):
            results = []
        latest_item = results[0] if results and isinstance(results[0], dict) else {}
        return {
            "ok": True,
            "backend": self.name,
            "source": "screenpipe",
            "query": params,
            "count": len(results),
            "latest": self._summarize_result(latest_item) if latest_item else {},
            "results": results[: int(params.get("limit", 1) or 1)],
            "pagination": data.get("pagination", {}) if isinstance(data, dict) else {},
        }


class HammerspoonAdapter(SystemBackendAdapter):
    """Local macOS control via Hammerspoon when the `hs` CLI is available."""

    name = "hammerspoon"

    def available(self) -> bool:
        return bool(_which("hs"))

    def _run_lua(self, code: str) -> dict[str, Any]:
        binary = _which("hs")
        if not binary:
            raise BackendUnavailable("hs CLI is unavailable")
        proc = subprocess.run(
            [binary, "-c", code],
            text=True,
            capture_output=True,
            timeout=20.0,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
        }

    def desktop_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = _safe_text(payload.get("action", ""))
        if action in {"click", "double_click"}:
            point = payload.get("point") or {}
            x = int(point.get("x", payload.get("x", 0)) or 0)
            y = int(point.get("y", payload.get("y", 0)) or 0)
            button = "1"
            click_count = "2" if action == "double_click" else "1"
            code = (
                "local p = {x = %d, y = %d} "
                "hs.eventtap.leftClick(p, nil, %s)"
                % (x, y, click_count)
            )
            return self._run_lua(code)
        if action in {"type", "type_text"}:
            text = json.dumps(_safe_text(payload.get("text", payload.get("value", ""))))
            return self._run_lua(f"hs.eventtap.keyStrokes({text})")
        if action in {"press", "press_key"}:
            key = json.dumps(_safe_text(payload.get("key", payload.get("text", ""))))
            modifiers = payload.get("modifiers", [])
            mods = "{" + ",".join(json.dumps(_safe_text(m)) for m in modifiers if m) + "}"
            return self._run_lua(f"hs.eventtap.keyStroke({mods}, {key})")
        raise BackendUnavailable(f"hammerspoon cannot handle desktop_control action '{action}'")

    def system_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = _safe_text(payload.get("action", ""))
        if action in {"open_app", "focus_app"}:
            app = json.dumps(_safe_text(payload.get("app", payload.get("target", ""))))
            return self._run_lua(f"hs.application.launchOrFocus({app})")
        if action in {"lock_screen", "screen_lock"}:
            return self._run_lua("hs.caffeinate.lockScreen()")
        if action in {"sleep_display", "display_sleep"}:
            return self._run_lua("hs.caffeinate.startScreensaver()")
        raise BackendUnavailable(f"hammerspoon cannot handle system_action '{action}'")

    def screen_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        code = r'''
local app = hs.application.frontmostApplication()
local win = hs.window.frontmostWindow()
local screen = hs.screen.mainScreen()
local frame = screen and screen:frame() or nil
local result = {
  active_app = app and app:name() or "",
  window_title = win and win:title() or "",
  screen = frame and {x = frame.x, y = frame.y, w = frame.w, h = frame.h} or {},
}
print(hs.json.encode(result))
'''
        data = self._run_lua(code)
        try:
            if data.get("stdout"):
                parsed = json.loads(str(data["stdout"]).strip().splitlines()[-1])
                if isinstance(parsed, dict):
                    parsed["backend"] = self.name
                    parsed["ok"] = data.get("ok", False)
                    return parsed
        except Exception:
            pass
        return {"backend": self.name, **data}


class LocalAdapter(SystemBackendAdapter):
    """Portable fallback for the host machine."""

    name = "local"

    def available(self) -> bool:
        return True

    def desktop_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = _safe_text(payload.get("action", ""))
        point = payload.get("point") or {}
        x = int(point.get("x", payload.get("x", 0)) or 0)
        y = int(point.get("y", payload.get("y", 0)) or 0)
        text = _safe_text(payload.get("text", payload.get("value", "")))
        key = _safe_text(payload.get("key", text))
        modifiers = [str(m) for m in payload.get("modifiers", []) if m]

        try:
            import pyautogui  # type: ignore

            if action in {"click", "double_click"}:
                pyautogui.click(x=x, y=y, clicks=2 if action == "double_click" else 1)
            elif action in {"type", "type_text"}:
                pyautogui.write(text, interval=0.01)
            elif action in {"press", "press_key"}:
                pyautogui.hotkey(*([*modifiers, key] if modifiers else [key]))
            elif action in {"drag", "drag_to"}:
                pyautogui.dragTo(x, y, duration=float(payload.get("duration", 0.2)))
            else:
                raise BackendUnavailable(f"unsupported desktop_control action '{action}'")
            return {"ok": True, "backend": self.name, "action": action}
        except Exception:
            if action in {"click", "double_click"} and _which("osascript"):
                script = f'tell application "System Events" to click at {{{x}, {y}}}'
                if action == "double_click":
                    return {
                        "backend": self.name,
                        **_run_osascript(script + '\n' + script),
                        "action": action,
                    }
                return {"backend": self.name, **_run_osascript(script), "action": action}
            if action in {"type", "type_text"}:
                escaped = text.replace("\\", "\\\\").replace('"', '\\"')
                script = f'tell application "System Events" to keystroke "{escaped}"'
                return {"backend": self.name, **_run_osascript(script), "action": action}
            if action in {"press", "press_key"}:
                modifiers_text = ""
                if modifiers:
                    applescript_mods = " using {" + ", ".join(f"{_apple_script_modifier(m)} down" for m in modifiers) + "}"
                    modifiers_text = applescript_mods
                script = f'tell application "System Events" to keystroke "{key}"{modifiers_text}'
                return {"backend": self.name, **_run_osascript(script), "action": action}
            raise

    def desktop_exec(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = payload.get("command")
        if command is None:
            command = payload.get("argv")
        cwd = _safe_text(payload.get("cwd", ""))
        timeout = float(payload.get("timeout", 30.0) or 30.0)
        shell = bool(payload.get("shell", isinstance(command, str)))
        if isinstance(command, list):
            args = [str(part) for part in command]
        elif isinstance(command, str):
            args = [command]
        else:
            raise BackendUnavailable("command or argv is required")
        result = _run_subprocess(args, timeout=timeout, cwd=cwd, shell=shell)
        result["backend"] = self.name
        result["command"] = command
        return result

    def system_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = _safe_text(payload.get("action", ""))
        app = _safe_text(payload.get("app", payload.get("target", "")))
        url = _safe_text(payload.get("url", ""))
        path = _safe_text(payload.get("path", ""))

        if action in {"open_app", "focus_app"} and app:
            opener = _which("open")
            if opener:
                return {"backend": self.name, **_run_subprocess([opener, "-a", app]), "action": action, "app": app}
            script = f'tell application "{app}" to activate'
            return {"backend": self.name, **_run_osascript(script), "action": action, "app": app}
        if action == "open_url" and url:
            opener = _which("open", "xdg-open")
            if opener:
                return {"backend": self.name, **_run_subprocess([opener, url]), "action": action, "url": url}
        if action in {"lock_screen", "screen_lock"}:
            if platform.system().lower() == "darwin":
                script = 'tell application "System Events" to keystroke "q" using {control down, command down}'
                return {"backend": self.name, **_run_osascript(script), "action": action}
        if action in {"sleep_display", "display_sleep"}:
            if platform.system().lower() == "darwin":
                return {"backend": self.name, **_run_subprocess(["pmset", "displaysleepnow"]), "action": action}
        if action == "open_path" and path:
            opener = _which("open", "xdg-open")
            if opener:
                return {"backend": self.name, **_run_subprocess([opener, path]), "action": action, "path": path}
        raise BackendUnavailable(f"unsupported system_action '{action}'")

    def screen_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        include_screenshot = bool(payload.get("include_screenshot", True))
        screenshot_path = ""
        if include_screenshot:
            shot = Path(tempfile.gettempdir()) / f"system-executor-screen-{int(time.time())}.png"
            screencapture = _which("screencapture")
            if screencapture:
                _run_subprocess([screencapture, "-x", str(shot)])
                if shot.exists():
                    screenshot_path = str(shot)

        front_app = ""
        window_title = ""
        if platform.system().lower() == "darwin" and _which("osascript"):
            app_result = _run_osascript('tell application "System Events" to get name of first application process whose frontmost is true')
            front_app = str(app_result.get("stdout", "")).strip().splitlines()[-1] if app_result.get("stdout") else ""
            window_result = _run_osascript(
                'tell application "System Events" to tell (first application process whose frontmost is true) '
                'to try\nset theTitle to name of front window\nreturn theTitle\nend try'
            )
            window_title = str(window_result.get("stdout", "")).strip().splitlines()[-1] if window_result.get("stdout") else ""

        screen_size = {}
        try:
            import pyautogui  # type: ignore

            size = pyautogui.size()
            screen_size = {"width": int(size.width), "height": int(size.height)}
        except Exception:
            pass

        return {
            "backend": self.name,
            "ok": True,
            "platform": platform.platform(),
            "frontmost_app": front_app,
            "window_title": window_title,
            "screen": screen_size,
            "screenshot_path": screenshot_path,
        }


@dataclass
class CheckpointRecord:
    checkpoint_id: str
    status: str
    purpose: str = ""
    requested_by: str = ""
    created_at: float = field(default_factory=time.time)
    resolved_at: float = 0.0
    resolution: dict[str, Any] = field(default_factory=dict)


class SystemExecutorRuntime:
    """Selects the best adapter for each capability."""

    def __init__(self, adapters: list[SystemBackendAdapter] | None = None):
        self._adapters = adapters or self._build_adapters()

    def _build_adapters(self) -> list[SystemBackendAdapter]:
        preferred = [item.strip().lower() for item in _env("SYSTEM_EXECUTOR_BACKENDS", "peekaboo,screenpipe,cua,hammerspoon,local").split(",") if item.strip()]
        pool = {
            "screenpipe": ScreenpipeAdapter(),
            "peekaboo": PeekabooAdapter(),
            "cua": CuaAdapter(),
            "hammerspoon": HammerspoonAdapter(),
            "local": LocalAdapter(),
        }
        adapters = [pool[name] for name in preferred if name in pool]
        for name, adapter in pool.items():
            if adapter not in adapters:
                adapters.append(adapter)
        return adapters

    def active_backend(self, capability: str | None = None) -> str:
        adapter = self._pick(capability or "health_check", ready_only=True)
        if adapter is None:
            adapter = self._pick(capability or "health_check", ready_only=False)
        return adapter.name if adapter else "none"

    def _pick(self, capability: str, *, ready_only: bool = False) -> SystemBackendAdapter | None:
        for adapter in self._adapters:
            if not adapter.available() or not adapter.supports(capability):
                continue
            if ready_only and not adapter.ready():
                continue
            return adapter
        return None

    def backend_order(self) -> list[str]:
        return [adapter.name for adapter in self._adapters]

    async def status(self, capability: str = "health_check") -> dict[str, Any]:
        adapters = [adapter.health() for adapter in self._adapters]
        selected_backend = self.active_backend(capability)
        preferred_backend = next(
            (
                adapter.name
                for adapter in self._adapters
                if adapter.available() and adapter.supports(capability)
            ),
            "none",
        )
        fallback_backend = next(
            (
                adapter.name
                for adapter in reversed(self._adapters)
                if adapter.available() and adapter.supports(capability)
            ),
            "none",
        )
        return {
            "capability": capability,
            "backend_order": self.backend_order(),
            "preferred_backend": preferred_backend,
            "selected_backend": selected_backend,
            "fallback_backend": fallback_backend,
            "adapters": adapters,
        }

    async def _invoke(self, capability: str, method_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        last_error: dict[str, Any] = {"ok": False, "error": f"no {capability} backend available"}
        for adapter in self._adapters:
            if not adapter.available() or not adapter.supports(capability):
                continue
            try:
                if not adapter.ready():
                    last_error = {"ok": False, "backend": adapter.name, "error": "backend not ready"}
                    continue
            except Exception as exc:
                logger.debug("Adapter %s readiness check failed for %s: %s", adapter.name, capability, exc, exc_info=True)
                last_error = {"ok": False, "backend": adapter.name, "error": str(exc)}
                continue
            try:
                method = getattr(adapter, method_name)
                result = await asyncio.to_thread(method, payload)
                if isinstance(result, dict) and result.get("ok") is False and result.get("error"):
                    last_error = result
                    continue
                return result if isinstance(result, dict) else {"ok": True, "backend": adapter.name, "result": result}
            except BackendUnavailable as exc:
                last_error = {"ok": False, "backend": adapter.name, "error": str(exc)}
                continue
            except Exception as exc:
                logger.debug("Adapter %s failed for %s: %s", adapter.name, capability, exc, exc_info=True)
                last_error = {"ok": False, "backend": adapter.name, "error": str(exc)}
                continue
        return last_error

    async def health(self) -> dict[str, Any]:
        return await self.status("health_check")

    async def desktop_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._invoke("desktop_control", "desktop_control", payload)

    async def desktop_exec(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._invoke("desktop_exec", "desktop_exec", payload)

    async def system_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._invoke("system_action", "system_action", payload)

    async def screen_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._invoke("screen_context", "screen_context", payload)


__all__ = [
    "BackendUnavailable",
    "CuaAdapter",
    "HammerspoonAdapter",
    "LocalAdapter",
    "PeekabooAdapter",
    "CheckpointRecord",
    "ScreenpipeAdapter",
    "SystemExecutorRuntime",
    "SystemBackendAdapter",
]
