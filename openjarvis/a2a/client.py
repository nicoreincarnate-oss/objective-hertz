"""A2A client — discover and call external A2A agents."""

from __future__ import annotations

from typing import Any

from openjarvis.a2a.protocol import A2ARequest, A2ATask, AgentCard


class A2AClient:
    """Client for calling external A2A-compatible agents.

    Discovers agent capabilities via /.well-known/agent.json and
    sends tasks via /a2a/tasks.
    """

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._card: AgentCard | None = None

    def discover(self) -> AgentCard:
        """Fetch the agent card from /.well-known/agent.json."""
        import httpx
        resp = httpx.get(
            f"{self._base_url}/.well-known/agent.json",
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self._card = AgentCard(
            name=data.get("name", ""),
            description=data.get("description", ""),
            url=data.get("url", self._base_url),
            version=data.get("version", ""),
            capabilities=data.get("capabilities", []),
            skills=data.get("skills", []),
        )
        return self._card

    def send_task(
        self,
        input_text: str,
        *,
        request_id: str = "",
        headers: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> A2ATask:
        """Send a task to the remote agent and return the result.

        Args:
            timeout: Per-call timeout in seconds. Overrides the client's
                     default timeout for this single request.
        """
        import httpx
        request_kwargs: dict[str, Any] = {
            "method": "tasks/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"text": input_text}],
                },
                "metadata": metadata or {},
            },
        }
        if request_id:
            request_kwargs["request_id"] = request_id
        request = A2ARequest(**request_kwargs)
        effective_timeout = timeout if timeout is not None else self._timeout
        resp = httpx.post(
            f"{self._base_url}/a2a/tasks",
            json=request.to_dict(),
            headers=headers or {},
            timeout=effective_timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", {})
        return A2ATask(
            task_id=result.get("id", ""),
            state=result.get("state", "unknown"),
            input_text=result.get("input", input_text),
            output_text=result.get("output", ""),
            history=result.get("history", []),
        )

    def get_task(self, task_id: str, *, timeout: float | None = None) -> A2ATask:
        """Get the status of a previously submitted task."""
        import httpx
        request = A2ARequest(
            method="tasks/get",
            params={"id": task_id},
        )
        effective_timeout = timeout if timeout is not None else self._timeout
        resp = httpx.post(
            f"{self._base_url}/a2a/tasks",
            json=request.to_dict(),
            timeout=effective_timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", {})
        return A2ATask(
            task_id=result.get("id", task_id),
            state=result.get("state", "unknown"),
            output_text=result.get("output", ""),
        )

    def cancel_task(self, task_id: str) -> A2ATask:
        """Cancel a running task."""
        import httpx
        request = A2ARequest(
            method="tasks/cancel",
            params={"id": task_id},
        )
        resp = httpx.post(
            f"{self._base_url}/a2a/tasks",
            json=request.to_dict(),
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", {})
        return A2ATask(
            task_id=result.get("id", task_id),
            state=result.get("state", "canceled"),
        )


__all__ = ["A2AClient"]
