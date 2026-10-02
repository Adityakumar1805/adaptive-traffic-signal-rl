"""Small runtime helpers shared by both dashboard servers: JSON encoding, per-tick statistics,
process CPU and memory, token-bucket rate limiting and static-file responses."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from collections import deque
from functools import lru_cache
from typing import Any, Callable, Deque, Dict, Optional, Tuple

from atsc.dashboard.assets import build_id, content_type, resolve_static

try:  # orjson ships with FastAPI 0.111's dependency set; json is the fallback
    import orjson as _orjson
except Exception:  # pragma: no cover - depends on the environment
    _orjson = None


def encode(obj: Any) -> str:
    """Compact JSON text (no spaces). Frames never contain NaN/inf by construction."""
    if _orjson is not None:
        return _orjson.dumps(obj).decode("utf-8")
    return json.dumps(obj, separators=(",", ":"), allow_nan=False)


# --------------------------------------------------------------------------- #
# statistics for /healthz
# --------------------------------------------------------------------------- #
def _summary(values) -> Dict[str, float]:
    if not values:
        return {"mean": 0.0, "p95": 0.0, "max": 0.0}
    data = sorted(values)
    return {"mean": round(sum(data) / len(data), 3),
            "p95": round(data[min(len(data) - 1, int(0.95 * len(data)))], 3),
            "max": round(data[-1], 3)}


CPU_WINDOW_S = 30          # /healthz reports the process CPU use over about this long


class TickStats:
    """Rolling window of server tick cost and broadcast payload size, plus the process's
    CPU use (all threads, as a percentage of one core) sampled once a second."""

    def __init__(self, window: int = 600, wall_clock: Callable[[], float] = time.monotonic,
                 cpu_clock: Callable[[], float] = time.process_time) -> None:
        self._lock = threading.Lock()
        self.tick_ms: Deque[float] = deque(maxlen=window)
        self.payload_bytes: Deque[int] = deque(maxlen=window)
        self.started = time.time()
        self.frames = 0
        self.keyframes = 0
        self.errors = 0
        self._wall, self._cpu_clock = wall_clock, cpu_clock
        self._cpu_start = (wall_clock(), cpu_clock())
        self._cpu: Deque[Tuple[float, float]] = deque([self._cpu_start], maxlen=CPU_WINDOW_S + 1)

    def record_tick(self, ms: float) -> None:
        with self._lock:
            self.tick_ms.append(ms)
            now = self._wall()
            if now - self._cpu[-1][0] >= 1.0:
                self._cpu.append((now, self._cpu_clock()))

    def cpu_percent(self) -> Dict[str, Optional[float]]:
        """CPU time used by the whole process per wall-clock second, in % of one core:
        over the last ~30 s of ticks and since start. A free Render instance has 0.1 CPU,
        so a value pinned near 10 means the process is being throttled."""
        now, cpu = self._wall(), self._cpu_clock()

        def pct(since: Tuple[float, float]) -> Optional[float]:
            wall = now - since[0]
            return round(100.0 * (cpu - since[1]) / wall, 1) if wall >= 0.5 else None

        with self._lock:
            return {"last_30s": pct(self._cpu[0]), "since_start": pct(self._cpu_start)}

    def record_payload(self, nbytes: int, keyframe: bool) -> None:
        with self._lock:
            self.payload_bytes.append(nbytes)
            self.frames += 1
            self.keyframes += int(keyframe)

    def record_error(self) -> None:
        with self._lock:
            self.errors += 1

    def snapshot(self) -> Dict[str, Any]:
        cpu = self.cpu_percent()
        with self._lock:
            return {"uptime_s": int(time.time() - self.started),
                    "tick_ms": _summary(list(self.tick_ms)),
                    "payload_bytes": _summary(list(self.payload_bytes)),
                    "frames_sent": self.frames, "keyframes_sent": self.keyframes,
                    "tick_errors": self.errors, "cpu_percent": cpu}


def memory_mb() -> Dict[str, Optional[float]]:
    """Current and peak resident memory of this process, in MiB (``None`` if unknown)."""
    rss = peak = None
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) / 1024.0
                elif line.startswith("VmHWM:"):
                    peak = int(line.split()[1]) / 1024.0
    except OSError:
        try:
            import resource
            maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            peak = maxrss / (1024.0 * 1024.0) if sys.platform == "darwin" else maxrss / 1024.0
        except Exception:  # pragma: no cover - platform specific
            pass
    return {"rss_mb": None if rss is None else round(rss, 1),
            "peak_rss_mb": None if peak is None else round(peak, 1)}


# --------------------------------------------------------------------------- #
# rate limiting
# --------------------------------------------------------------------------- #
class TokenBucket:
    """``rate`` tokens per second, at most ``burst`` saved up. Thread-safe."""

    def __init__(self, rate: float, burst: float) -> None:
        self.rate, self.burst = float(rate), float(burst)
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


# --------------------------------------------------------------------------- #
# static files
# --------------------------------------------------------------------------- #
NO_CACHE = "no-cache"

#: The page loads nothing from other origins; the socket goes back to the same host.
CONTENT_SECURITY_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self'; "
                           "img-src 'self' data: blob:; connect-src 'self' ws: wss:; "
                           "worker-src 'self'; object-src 'none'; base-uri 'none'; "
                           "form-action 'none'")


def page_headers() -> Dict[str, str]:
    """Headers for ``/``: revalidate on every visit, build id (the service worker only caches
    responses that carry it), and a strict same-origin content security policy."""
    return {"Cache-Control": NO_CACHE, "X-ATSC-Build": build_id(),
            "Content-Security-Policy": CONTENT_SECURITY_POLICY,
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


@lru_cache(maxsize=64)
def _static_entry(rel: str) -> Optional[Tuple[bytes, str, str]]:
    path = resolve_static(rel)
    if path is None:
        return None
    body = path.read_bytes()
    etag = '"' + hashlib.sha256(body).hexdigest()[:20] + '"'
    return body, etag, content_type(path)


def static_response(rel: str, if_none_match: Optional[str]) -> Tuple[int, Dict[str, str], bytes]:
    """``(status, headers, body)`` for ``/static/<rel>``: 200, 304 (ETag match) or 404.

    Files are revalidated on every load (``Cache-Control: no-cache``) so a redeploy is picked
    up immediately, while unchanged files cost only a 304.
    """
    entry = _static_entry(rel)
    if entry is None:
        return 404, {"Content-Type": "text/plain; charset=utf-8"}, b"not found"
    body, etag, ctype = entry
    headers = {"Content-Type": ctype, "Cache-Control": NO_CACHE, "ETag": etag,
               "X-Content-Type-Options": "nosniff", "X-ATSC-Build": build_id()}
    if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
        return 304, headers, b""
    return 200, headers, body


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")
