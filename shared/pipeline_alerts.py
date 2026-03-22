"""Shared helpers for surfacing pipeline failures as Hermes-visible events."""

from shared.db import emit_event


async def emit_pipeline_error(
    stage: str,
    error: Exception,
    *,
    lead_id: int | None = None,
    client_id: int | None = None,
    context: dict[str, object] | None = None,
    **extra: object,
) -> None:
    """Emit a structured pipeline error event for Hermes/ops visibility."""
    payload: dict[str, object] = {
        "stage": stage,
        "error": str(error)[:300],
        "error_type": type(error).__name__,
    }
    if lead_id is not None:
        payload["lead_id"] = lead_id
    if client_id is not None:
        payload["client_id"] = client_id
    if context:
        payload.update(context)
    if extra:
        payload.update(extra)

    await emit_event("pipeline_error", payload)
