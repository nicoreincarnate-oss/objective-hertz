"""Small Mem0-compatible HTTP service used by local Docker runtime.

The production code only relies on a narrow API surface:
- POST /v1/memories/
- POST /v1/memories/search/
- DELETE /v1/memories/{id}/
- GET /health

This service keeps that contract local and deterministic so the stack can
boot from source without depending on an external Mem0 server.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field


DB_PATH = Path(os.environ.get("MEM0_HISTORY_DB_PATH", "/data/history.db"))
app = FastAPI(title="Perseus Mem0 Compat", version="1.0")


class Message(BaseModel):
    role: str = "assistant"
    content: str


class StoreRequest(BaseModel):
    messages: list[Message] = Field(default_factory=list)
    user_id: str
    metadata: dict = Field(default_factory=dict)


class SearchRequest(BaseModel):
    query: str
    user_id: str
    limit: int = 5


def _ensure_parent() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    _ensure_parent()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> set[str]:
    return {part for part in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if part}


def _score(query: str, content: str, metadata: dict) -> float:
    query_tokens = _tokenize(query)
    content_tokens = _tokenize(content)
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & content_tokens) / len(query_tokens)
    bonus = 0.15 if query.lower() in content.lower() else 0.0
    category = str(metadata.get("category", "")).lower()
    if category and category in query.lower():
        bonus += 0.1
    return min(1.0, overlap + bonus)


@app.get("/health")
@app.get("/api/v1/health")
async def health() -> dict:
    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    return {"status": "ok", "memories": count}


@app.post("/v1/memories/")
async def store_memory(payload: StoreRequest) -> dict:
    content = "\n".join(msg.content.strip() for msg in payload.messages if msg.content.strip()).strip()
    if not content:
        raise HTTPException(status_code=400, detail="messages must include content")

    memory_id = str(uuid.uuid4())
    created_at = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO memories (id, user_id, content, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                payload.user_id,
                content,
                json.dumps(payload.metadata or {}, sort_keys=True),
                created_at,
            ),
        )

    return {"id": memory_id, "user_id": payload.user_id, "created_at": created_at}


@app.post("/v1/memories/search/")
async def search_memories(payload: SearchRequest) -> dict:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, content, metadata_json, created_at
            FROM memories
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (payload.user_id,),
        ).fetchall()

    ranked = []
    for row in rows:
        metadata = json.loads(row["metadata_json"])
        score = _score(payload.query, row["content"], metadata)
        if score <= 0:
            continue
        ranked.append(
            {
                "id": row["id"],
                "memory": row["content"],
                "score": round(score, 4),
                "metadata": metadata,
                "created_at": row["created_at"],
            }
        )

    ranked.sort(key=lambda item: (item["score"], item["created_at"]), reverse=True)
    return {"results": ranked[: max(1, payload.limit)]}


@app.delete("/v1/memories/{memory_id}/")
async def delete_memory(memory_id: str, user_id: str = Query(...)) -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM memories WHERE id = ? AND user_id = ?",
            (memory_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="memory not found")
        conn.execute("DELETE FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id))

    return {"deleted": True, "id": memory_id}
