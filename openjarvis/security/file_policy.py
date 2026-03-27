"""File sensitivity policy — block access to secrets, credentials, and keys."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

DEFAULT_SENSITIVE_PATTERNS: frozenset[str] = frozenset({
    ".env",
    ".env.*",
    "*.env",
    ".secret",
    "*.secrets",
    "credentials.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "id_rsa",
    "id_ed25519",
    ".htpasswd",
    ".pgpass",
    ".netrc",
})


def is_sensitive_file(path: str | Path) -> bool:
    """Return ``True`` if *path* matches a sensitive file pattern.

    Checks both the filename and the full name against
    ``DEFAULT_SENSITIVE_PATTERNS`` using :func:`fnmatch.fnmatch`.
    """
    import fnmatch as _fnmatch

    from openjarvis._rust_bridge import get_rust_module

    _rust = get_rust_module()
    if _rust is None:
        name = Path(path).name
        return any(
            _fnmatch.fnmatch(name, pat) or _fnmatch.fnmatch(str(path), pat)
            for pat in DEFAULT_SENSITIVE_PATTERNS
        )
    return _rust.is_sensitive_file(str(path))


def filter_sensitive_paths(paths: Iterable[str | Path]) -> list[Path]:
    """Return only non-sensitive paths from *paths*."""
    return [Path(p) for p in paths if not is_sensitive_file(p)]


__all__ = [
    "DEFAULT_SENSITIVE_PATTERNS",
    "filter_sensitive_paths",
    "is_sensitive_file",
]
