"""Bootstrap MAGMA from existing soul/knowledge markdown files (Phase 42 A1).

One-shot ingestion script that scans the repository's soul/, daemon SOUL.md,
daemon knowledge/, and the user's global Perseus memory directory for markdown
files, then creates MAGMA nodes (Neo4j) + ``memory_provenance`` rows (Postgres)
for each new file. Idempotent: rerunning skips files whose SHA-256 already
exists in ``memory_provenance``.

After ingestion, computes pairwise cosine similarity for the most recently
inserted nodes and adds ``RELATED_TO`` edges in Neo4j for any pair scoring
above 0.7.

Usage:
    python scripts/bootstrap_magma.py [--dry-run] [--verbose] [--limit N]
                                      [--source PATH] ...

Constraints (Phase 42 A1):
    - CREATE-only: this script does not modify shared/magma.py or shared/db.py.
    - Uses shared.db connection pool (no raw psycopg).
    - Ollama embedding failures are non-fatal — nodes are still created.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

# Ensure repo root is importable when invoked as `python scripts/bootstrap_magma.py`
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("bootstrap_magma")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
SIMILARITY_THRESHOLD = 0.7
SIMILARITY_WINDOW = 200

KNOWN_DAEMONS = (
    "perseus",
    "titan",
    "hermes",
    "clawdbot",
    "conway",
    "deerflow",
    "ruflo",
    "openjarvis",
)

GLOBAL_MEMORY_ROOT = Path.home() / ".claude/projects/-Users-majovega-Desktop-Projects-objective-hertz/memory"


@dataclass
class Stats:
    """Aggregated counters for the bootstrap run."""

    files_scanned: int = 0
    nodes_inserted: int = 0
    nodes_skipped: int = 0
    edges_inferred: int = 0
    errors: int = 0
    embed_failures: int = 0
    inserted_nodes: list[dict[str, Any]] = field(default_factory=list)


# ── File discovery ────────────────────────────────────────────────────────────


def default_scan_paths() -> list[Path]:
    """Return the default set of roots to walk for markdown files."""
    paths: list[Path] = []
    soul_root = _REPO_ROOT / "soul"
    if soul_root.exists():
        paths.append(soul_root)
    for daemon in KNOWN_DAEMONS:
        soul_md = _REPO_ROOT / daemon / "SOUL.md"
        if soul_md.exists():
            paths.append(soul_md)
        knowledge = _REPO_ROOT / daemon / "knowledge"
        if knowledge.exists():
            paths.append(knowledge)
    if GLOBAL_MEMORY_ROOT.exists():
        paths.append(GLOBAL_MEMORY_ROOT)
    return paths


def collect_markdown_files(roots: list[Path]) -> list[Path]:
    """Recursively collect .md files from each root, deduped by absolute path."""
    found: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            logger.warning("Skipping missing scan root: %s", root)
            continue
        if root.is_file():
            if root.suffix.lower() == ".md":
                found[str(root.resolve())] = root.resolve()
            continue
        for md in root.rglob("*.md"):
            try:
                resolved = md.resolve()
            except OSError:
                continue
            found[str(resolved)] = resolved
    return sorted(found.values())


# ── Metadata extraction ───────────────────────────────────────────────────────


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_FRONTMATTER_KEY_RE = re.compile(r"^\s*([A-Za-z0-9_\-]+)\s*:\s*(.+?)\s*$", re.MULTILINE)
_FIRST_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def parse_frontmatter(content: str) -> dict[str, str]:
    """Lightweight YAML-ish frontmatter parser (string scalars only)."""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}
    out: dict[str, str] = {}
    for key, value in _FRONTMATTER_KEY_RE.findall(match.group(1)):
        out[key.lower()] = value.strip().strip("\"'")
    return out


def extract_title(content: str, frontmatter: dict[str, str], path: Path) -> str:
    """Title precedence: frontmatter name → first heading → filename stem."""
    if "name" in frontmatter and frontmatter["name"]:
        return frontmatter["name"]
    heading = _FIRST_HEADING_RE.search(content)
    if heading:
        return heading.group(1).strip()
    return path.stem


def determine_source_daemon(path: Path) -> str:
    """Map a path to the owning daemon, or 'unknown'."""
    try:
        rel = path.resolve().relative_to(_REPO_ROOT)
        parts = rel.parts
        if parts and parts[0] == "soul":
            return "unknown"
        if parts and parts[0] in KNOWN_DAEMONS:
            return parts[0]
    except ValueError:
        pass
    try:
        rel = path.resolve().relative_to(GLOBAL_MEMORY_ROOT)
        parts = rel.parts
        if parts and parts[0] in KNOWN_DAEMONS:
            return parts[0]
    except ValueError:
        pass
    return "unknown"


def determine_domain(frontmatter: dict[str, str], path: Path, source_daemon: str) -> str:
    """Pick a domain from frontmatter, else from path segments."""
    if frontmatter.get("domain"):
        return frontmatter["domain"]
    try:
        rel = path.resolve().relative_to(_REPO_ROOT)
        parts = rel.parts
        if len(parts) >= 2 and parts[0] == "soul":
            return f"soul:{parts[1]}"
    except ValueError:
        pass
    if source_daemon != "unknown":
        return f"daemon:{source_daemon}"
    return "default"


def file_hash(content: bytes) -> str:
    """Return a SHA-256 hex digest of raw file bytes."""
    return hashlib.sha256(content).hexdigest()


# ── Embeddings ────────────────────────────────────────────────────────────────


async def fetch_embedding(client: httpx.AsyncClient, text: str) -> list[float] | None:
    """Call Ollama for an embedding. Returns None on failure (logged, non-fatal)."""
    try:
        response = await client.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": OLLAMA_EMBED_MODEL, "prompt": text[:8000]},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        emb = data.get("embedding")
        if isinstance(emb, list) and emb:
            return [float(x) for x in emb]
    except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Ollama embedding failed: %s", exc)
    return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def embedding_hash(embedding: list[float] | None) -> str:
    """Stable hex digest for an embedding vector (or empty string)."""
    if not embedding:
        return ""
    blob = ",".join(f"{x:.6f}" for x in embedding[:64]).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()  # noqa: S324 — non-security identifier hash


# ── Persistence ───────────────────────────────────────────────────────────────


async def already_indexed(file_hash_hex: str) -> bool:
    """Check whether a file (by hash) was previously bootstrapped."""
    from shared.db import fetch_one

    row = await fetch_one(
        """SELECT magma_node_id FROM memory_provenance
           WHERE source_table = %s AND source_record_id = %s
           LIMIT 1""",
        ("soul_or_knowledge_bootstrap", file_hash_hex),
    )
    return row is not None


async def insert_provenance(
    magma_node_id: str,
    source_daemon: str,
    file_hash_hex: str,
) -> None:
    """Write the provenance row tying our content hash to the MAGMA node id."""
    from shared.db import execute

    await execute(
        """INSERT INTO memory_provenance
               (magma_node_id, source_daemon, source_table, source_record_id, visibility, ingested_at)
           VALUES (%s, %s, %s, %s, %s, NOW())
           ON CONFLICT (magma_node_id) DO NOTHING""",
        (
            magma_node_id,
            source_daemon,
            "soul_or_knowledge_bootstrap",
            file_hash_hex,
            "tier1",
        ),
    )


def insert_neo4j_node(
    node_id: str,
    title: str,
    content: str,
    source_daemon: str,
    domain: str,
    file_hash_hex: str,
    embedding_hex: str,
    file_path: str,
) -> bool:
    """Create a MemoryNode in Neo4j (best-effort). Returns True on success."""
    try:
        from shared.magma import _get_driver  # type: ignore[attr-defined]
    except ImportError:
        logger.debug("shared.magma._get_driver unavailable; skipping Neo4j insert")
        return False
    driver = _get_driver()
    if driver is None:
        logger.debug("Neo4j driver unavailable; node %s recorded only in Postgres", node_id)
        return False
    try:
        with driver.session() as session:
            session.run(
                """MERGE (n:MemoryNode {node_id: $node_id})
                   ON CREATE SET
                       n.content = $content,
                       n.title = $title,
                       n.category = $category,
                       n.timestamp = $ts,
                       n.metadata = $meta_json,
                       n.embedding_hash = $emb_hash,
                       n.domain = $domain,
                       n.consolidated = false,
                       n.source = 'bootstrap'""",
                node_id=node_id,
                content=content[:4000],
                title=title[:200],
                category="soul_or_knowledge_bootstrap",
                ts=datetime.utcnow().isoformat(),
                meta_json=json.dumps(
                    {
                        "source_daemon": source_daemon,
                        "file_hash": file_hash_hex,
                        "file_path": file_path,
                        "bootstrap": True,
                    }
                ),
                emb_hash=embedding_hex,
                domain=domain,
            )
        return True
    except (RuntimeError, OSError, ConnectionError) as exc:
        logger.warning("Neo4j node insert failed for %s: %s", node_id, exc)
        return False


def insert_related_edge(src_node_id: str, dst_node_id: str, similarity: float) -> bool:
    """Create a RELATED_TO edge between two nodes in Neo4j."""
    try:
        from shared.magma import _get_driver  # type: ignore[attr-defined]
    except ImportError:
        return False
    driver = _get_driver()
    if driver is None:
        return False
    try:
        with driver.session() as session:
            session.run(
                """MATCH (a:MemoryNode {node_id: $src})
                   MATCH (b:MemoryNode {node_id: $dst})
                   MERGE (a)-[r:RELATED_TO]->(b)
                   ON CREATE SET r.similarity = $sim, r.source = 'bootstrap'""",
                src=src_node_id,
                dst=dst_node_id,
                sim=float(similarity),
            )
        return True
    except (RuntimeError, OSError, ConnectionError) as exc:
        logger.warning("Neo4j RELATED_TO edge insert failed (%s→%s): %s", src_node_id, dst_node_id, exc)
        return False


# ── Main ingestion loop ───────────────────────────────────────────────────────


async def ingest_file(
    path: Path,
    client: httpx.AsyncClient,
    stats: Stats,
    dry_run: bool,
) -> None:
    """Process a single markdown file end-to-end."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        stats.errors += 1
        return

    stats.files_scanned += 1
    file_hash_hex = file_hash(raw)

    if not dry_run:
        try:
            if await already_indexed(file_hash_hex):
                stats.nodes_skipped += 1
                logger.debug("Skipping already-indexed file: %s", path)
                return
        except Exception as exc:  # noqa: BLE001 — surface DB errors as run errors
            logger.error("Provenance lookup failed for %s: %s", path, exc)
            stats.errors += 1
            return

    try:
        content = raw.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Decode failed for %s: %s", path, exc)
        stats.errors += 1
        return

    frontmatter = parse_frontmatter(content)
    title = extract_title(content, frontmatter, path)
    source_daemon = determine_source_daemon(path)
    domain = determine_domain(frontmatter, path, source_daemon)
    node_id = f"magma_{uuid.uuid4().hex[:12]}"

    embedding: list[float] | None = None
    if not dry_run:
        embedding = await fetch_embedding(client, content)
        if embedding is None:
            stats.embed_failures += 1
    emb_hex = embedding_hash(embedding)

    if dry_run:
        logger.info(
            "[DRY] would ingest %s (daemon=%s domain=%s title=%r hash=%s)",
            path,
            source_daemon,
            domain,
            title[:60],
            file_hash_hex[:12],
        )
        return

    neo4j_ok = insert_neo4j_node(
        node_id=node_id,
        title=title,
        content=content,
        source_daemon=source_daemon,
        domain=domain,
        file_hash_hex=file_hash_hex,
        embedding_hex=emb_hex,
        file_path=str(path),
    )

    try:
        await insert_provenance(node_id, source_daemon, file_hash_hex)
    except Exception as exc:  # noqa: BLE001
        logger.error("Provenance insert failed for %s: %s", path, exc)
        stats.errors += 1
        return

    stats.nodes_inserted += 1
    stats.inserted_nodes.append(
        {
            "node_id": node_id,
            "embedding": embedding,
            "title": title,
            "neo4j_ok": neo4j_ok,
        }
    )
    logger.info(
        "Ingested %s -> %s (neo4j=%s, embed=%s)",
        path,
        node_id,
        "ok" if neo4j_ok else "skip",
        "ok" if embedding else "missing",
    )


def infer_related_edges(stats: Stats) -> None:
    """Compute pairwise similarity over the recent window and write edges."""
    recent = [n for n in stats.inserted_nodes if n.get("embedding")]
    recent = recent[-SIMILARITY_WINDOW:]
    if len(recent) < 2:
        return
    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            sim = cosine_similarity(recent[i]["embedding"], recent[j]["embedding"])
            if sim > SIMILARITY_THRESHOLD:
                if insert_related_edge(recent[i]["node_id"], recent[j]["node_id"], sim):
                    stats.edges_inferred += 1


# ── CLI ───────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the argparse namespace."""
    parser = argparse.ArgumentParser(
        description="Bootstrap MAGMA from soul/knowledge markdown files (Phase 42 A1).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Count and inspect only; no DB writes.")
    parser.add_argument(
        "--source",
        action="append",
        default=None,
        metavar="PATH",
        help="Override default scan paths (repeatable).",
    )
    parser.add_argument("--limit", type=int, default=0, help="Process at most N files (0 = unlimited).")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    """Top-level async entrypoint. Returns process exit code."""
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if args.source:
        roots = [Path(p).expanduser() for p in args.source]
    else:
        roots = default_scan_paths()

    logger.info("Scan roots: %s", [str(r) for r in roots])
    files = collect_markdown_files(roots)
    if args.limit and args.limit > 0:
        files = files[: args.limit]
    logger.info("Discovered %d markdown files", len(files))

    stats = Stats()

    if not args.dry_run:
        try:
            from shared.db import init_pool

            await init_pool(min_size=1, max_size=4)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to initialize DB pool: %s", exc)
            return 2

    try:
        async with httpx.AsyncClient() as client:
            for path in files:
                await ingest_file(path, client, stats, args.dry_run)
        if not args.dry_run:
            infer_related_edges(stats)
    finally:
        if not args.dry_run:
            try:
                from shared.db import close_pool as _close_pool

                await _close_pool()
            except Exception as exc:  # noqa: BLE001
                logger.warning("DB pool close failed: %s", exc)

    print("─" * 60)
    print("MAGMA bootstrap summary")
    print(f"  files scanned    : {stats.files_scanned}")
    print(f"  nodes inserted   : {stats.nodes_inserted}")
    print(f"  nodes skipped    : {stats.nodes_skipped}")
    print(f"  edges inferred   : {stats.edges_inferred}")
    print(f"  embed failures   : {stats.embed_failures}")
    print(f"  errors           : {stats.errors}")
    print("─" * 60)

    return 0 if stats.errors == 0 else 1


def main(argv: list[str] | None = None) -> int:
    """Synchronous CLI entrypoint."""
    args = parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
