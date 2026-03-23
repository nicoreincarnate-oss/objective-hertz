"""SQLite/FTS5 memory backend — zero-dependency default."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.events import EventType, get_event_bus
from openjarvis.core.registry import MemoryRegistry
from openjarvis.tools.storage._stubs import MemoryBackend, RetrievalResult


def _check_fts5(conn: sqlite3.Connection) -> bool:
    """Return True if the SQLite build includes FTS5."""
    try:
        opts = conn.execute("PRAGMA compile_options").fetchall()
        return any("FTS5" in o[0].upper() for o in opts)
    except sqlite3.Error:
        return False


@MemoryRegistry.register("sqlite")
class SQLiteMemory(MemoryBackend):
    """Full-text search memory backend using SQLite FTS5.

    Uses the built-in ``sqlite3`` module — no extra dependencies.
    """

    backend_id: str = "sqlite"

    def __init__(self, db_path: str | Path = "") -> None:
        if not db_path:
            from openjarvis.core.config import DEFAULT_CONFIG_DIR
            db_path = str(DEFAULT_CONFIG_DIR / "memory.db")

        self._db_path = str(db_path)
        self._rust_impl = None
        self._conn = None
        self._has_fts5 = False

        try:
            from openjarvis._rust_bridge import get_rust_module

            _rust = get_rust_module()
        except ImportError:
            _rust = None

        if _rust is not None:
            self._rust_impl = _rust.SQLiteMemory(self._db_path)
            return

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._has_fts5 = _check_fts5(self._conn)
        self._create_tables()
        self._conn.commit()

    def _create_tables(self) -> None:
        if self._conn is None:
            return

        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id       TEXT PRIMARY KEY,
                content  TEXT NOT NULL,
                source   TEXT NOT NULL DEFAULT '',
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
        """)
        if self._has_fts5:
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts
                USING fts5(
                    content,
                    source,
                    tokenize='porter unicode61'
                );
            """)

    def store(
        self,
        content: str,
        *,
        source: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Persist *content* and return a unique document id."""
        meta_json = json.dumps(metadata) if metadata else None
        if self._rust_impl is not None:
            doc_id = self._rust_impl.store(content, source, meta_json)
        else:
            if self._conn is None:
                raise RuntimeError("SQLite backend is not initialized")
            doc_id = str(uuid.uuid4())
            cursor = self._conn.execute(
                """
                INSERT INTO documents (id, content, source, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    content,
                    source,
                    meta_json or "{}",
                    time.time(),
                ),
            )
            if self._has_fts5:
                self._conn.execute(
                    """
                    INSERT INTO documents_fts (rowid, content, source)
                    VALUES (?, ?, ?)
                    """,
                    (cursor.lastrowid, content, source),
                )
            self._conn.commit()

        bus = get_event_bus()
        bus.publish(EventType.MEMORY_STORE, {
            "backend": self.backend_id,
            "doc_id": doc_id,
            "source": source,
        })
        return doc_id

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        **kwargs: Any,
    ) -> List[RetrievalResult]:
        """Search via FTS5 MATCH with BM25 ranking — always via Rust backend."""
        if not query.strip():
            return []
        if self._rust_impl is not None:
            from openjarvis._rust_bridge import retrieval_results_from_json

            results = retrieval_results_from_json(
                self._rust_impl.retrieve(query, top_k),
            )
        else:
            if self._conn is None:
                raise RuntimeError("SQLite backend is not initialized")
            if self._has_fts5:
                rows = self._conn.execute(
                    """
                    SELECT
                        d.content,
                        d.source,
                        d.metadata,
                        -bm25(documents_fts) AS score
                    FROM documents_fts
                    JOIN documents AS d ON d.rowid = documents_fts.rowid
                    WHERE documents_fts MATCH ?
                    ORDER BY bm25(documents_fts), d.created_at DESC
                    LIMIT ?
                    """,
                    (query, top_k),
                ).fetchall()
            else:
                terms = [term for term in query.split() if term]
                clauses = " OR ".join(
                    "(content LIKE ? OR source LIKE ?)" for _ in terms
                )
                params: list[Any] = []
                for term in terms:
                    like = f"%{term}%"
                    params.extend([like, like])
                params.append(top_k)
                rows = self._conn.execute(
                    f"""
                    SELECT content, source, metadata, 1.0 AS score
                    FROM documents
                    WHERE {clauses}
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()

            results = []
            for row in rows:
                metadata = row["metadata"] or "{}"
                results.append(
                    RetrievalResult(
                        content=row["content"],
                        score=max(float(row["score"]), 0.000001),
                        source=row["source"],
                        metadata=json.loads(metadata),
                    )
                )

        bus = get_event_bus()
        bus.publish(EventType.MEMORY_RETRIEVE, {
            "backend": self.backend_id,
            "query": query,
            "num_results": len(results),
        })
        return results

    def delete(self, doc_id: str) -> bool:
        """Delete a document by id — always via Rust backend."""
        if self._rust_impl is not None:
            return self._rust_impl.delete(doc_id)
        if self._conn is None:
            raise RuntimeError("SQLite backend is not initialized")

        row = self._conn.execute(
            "SELECT rowid FROM documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
        if row is None:
            return False
        self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        if self._has_fts5:
            self._conn.execute(
                "DELETE FROM documents_fts WHERE rowid = ?",
                (row["rowid"],),
            )
        self._conn.commit()
        return True

    def clear(self) -> None:
        """Remove all stored documents — always via Rust backend."""
        if self._rust_impl is not None:
            self._rust_impl.clear()
            return
        if self._conn is None:
            raise RuntimeError("SQLite backend is not initialized")
        self._conn.execute("DELETE FROM documents")
        if self._has_fts5:
            self._conn.execute("DELETE FROM documents_fts")
        self._conn.commit()

    def count(self) -> int:
        """Return the number of stored documents — always via Rust backend."""
        if self._rust_impl is not None:
            return self._rust_impl.count()
        if self._conn is None:
            raise RuntimeError("SQLite backend is not initialized")
        row = self._conn.execute("SELECT COUNT(*) AS count FROM documents").fetchone()
        return int(row["count"])

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


__all__ = ["SQLiteMemory"]
