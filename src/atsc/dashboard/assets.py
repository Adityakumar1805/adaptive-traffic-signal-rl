"""Static front-end assets shared by the FastAPI server and the stdlib fallback server.

* :data:`STATIC_DIR` holds ``index.html``, ``styles.css``, ``sw.js`` and ``js/*.js``.
* :func:`build_id` fingerprints those files (plus the protocol version) so a browser running
  an older cached copy of the page notices a redeploy and reloads itself once.
* :func:`resolve_static` maps a request path to a file *inside* ``STATIC_DIR`` or returns
  ``None`` - it is the single guard against path traversal for both servers.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Optional

STATIC_DIR = (Path(__file__).parent / "static").resolve()

#: bumped whenever the server <-> browser message format changes
PROTOCOL_VERSION = 2

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
}

_BUILD_PLACEHOLDER = "__ATSC_BUILD__"
_TRANSPORT_PLACEHOLDER = "__ATSC_TRANSPORT__"


@lru_cache(maxsize=1)
def build_id() -> str:
    """12-hex-digit fingerprint of every static file and the protocol version."""
    h = hashlib.sha256(f"protocol:{PROTOCOL_VERSION}".encode())
    for path in sorted(p for p in STATIC_DIR.rglob("*") if p.is_file()):
        h.update(path.relative_to(STATIC_DIR).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


@lru_cache(maxsize=2)
def index_html(transport: str = "ws") -> bytes:
    """``index.html`` with the build id and the server's transport filled in (cached; the
    files never change at runtime). ``transport="poll"`` (the stdlib server, which has no
    WebSocket) makes the page poll straight away instead of trying ``/ws`` first."""
    if transport not in ("ws", "poll"):
        raise ValueError(f"transport must be 'ws' or 'poll', got {transport!r}")
    text = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return (text.replace(_BUILD_PLACEHOLDER, build_id())
                .replace(_TRANSPORT_PLACEHOLDER, transport).encode("utf-8"))


@lru_cache(maxsize=1)
def service_worker_js() -> bytes:
    """``sw.js`` with the build id filled in, served from the site root so it controls ``/``."""
    text = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    return text.replace(_BUILD_PLACEHOLDER, build_id()).encode("utf-8")


def resolve_static(rel: str) -> Optional[Path]:
    """Return the file for ``/static/<rel>`` if it is a real file inside :data:`STATIC_DIR`.

    Rejects absolute paths, ``..`` segments, backslashes, NUL bytes and anything that resolves
    (through symlinks or otherwise) outside the static directory.
    """
    if not rel or "\x00" in rel or "\\" in rel or rel.startswith("/"):
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    try:
        candidate = (STATIC_DIR / rel).resolve()
    except (OSError, RuntimeError):
        return None
    if STATIC_DIR not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
