"""Keep a physical signal board in step with the live dashboard.

The dashboard simulates in bursts: each 200 ms tick runs one whole 5-second decision (or
several) in a few milliseconds and then waits. A browser smooths that over by replaying the
per-second lamp history, and real lamps need the same treatment, or the 3 s amber and 2 s
all-red would flash by in microseconds. :class:`HardwareMirror` therefore

* receives every simulated second's signal states with their simulation time
  (:meth:`push`, called from the environment's signal sink),
* plays them back on the wall clock at the current playback rate — one simulated second
  per real second at "real time" — from a background thread (:meth:`step`),
* reads the board's messages (detections, queue reports, remote buttons) on the same
  thread and hands them to the session (:meth:`take_events`),
* sends a few lines of live figures for the board's OLED (:meth:`set_text`), and
* reopens the port by itself if the USB cable is pulled and plugged back in.

The session never waits for the board: :meth:`push`, :meth:`set_rate`, :meth:`set_text`
and :meth:`take_events` only touch in-memory queues.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Sequence, Tuple

from atsc.hw.bridge import HardwareBridge
from atsc.hw.link import Link
from atsc.hw.protocol import TEXT_ROWS, UplinkEvent
from atsc.logging_utils import get_logger

log = get_logger("atsc.hw")

Aspects = Tuple[Tuple[str, int], ...]

#: the node counts as "live" while its heartbeat (once a second) is this fresh
LIVE_WITHIN_S = 3.0
#: most wall-clock time one playback step may cover (a step normally covers 20 ms)
MAX_STEP_S = 0.25


class HardwareMirror:
    """Plays the simulation's lamp states out to a :class:`HardwareBridge` in real time."""

    def __init__(self, bridge: HardwareBridge, decision_s: float = 5.0,
                 reopen: Optional[Callable[[], Link]] = None, reconnect_s: float = 2.0,
                 poll_s: float = 0.02, clock: Callable[[], float] = time.monotonic,
                 max_queue: int = 4000) -> None:
        self.bridge = bridge
        self.decision_s = float(decision_s)
        self._reopen = reopen
        self._reconnect_s = float(reconnect_s)
        self._poll_s = float(poll_s)
        self._clock = clock

        self._lock = threading.Lock()          # queues shared with the session thread
        self._io = threading.Lock()            # the bridge (used from one thread at a time)
        self._samples: Deque[Tuple[float, Aspects]] = deque(maxlen=max_queue)
        self._events: Deque[UplinkEvent] = deque(maxlen=512)
        self._text: List[Optional[str]] = [None] * TEXT_ROWS   # None: nothing to show yet
        self._rate = 1.0                       # simulated seconds per wall-clock second
        self._play_t: Optional[float] = None   # simulated time the lamps are showing
        self._cur: Optional[Tuple[float, Aspects]] = None
        self._last_wall: Optional[float] = None
        self._next_reopen = 0.0
        self.reconnects = 0
        self.last_error: Optional[str] = None
        self.skipped = 0                       # seconds dropped to catch up (speed changes)

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # called by the session (never blocks on the board)
    # ------------------------------------------------------------------ #
    def push(self, sim_t: float, aspects: Sequence[Tuple[str, int]]) -> None:
        """One simulated second's ``(state_value, current_phase)`` per junction."""
        sample = (float(sim_t), tuple((str(s), int(p)) for s, p in aspects))
        with self._lock:
            newest = self._samples[-1][0] if self._samples else (
                self._cur[0] if self._cur is not None else None)
            if newest is not None and sample[0] < newest:
                # a new episode (time went backwards): drop the old one's backlog and
                # show the new opening aspects straight away
                self._samples.clear()
                self._play_t = None
                self._last_wall = None
            elif newest is not None and sample[0] == newest:
                # the same second again: an episode's opening aspects are published once
                # more, after the first decision, by its first second. The later state is
                # the one that lasts that second, so it gets the whole second.
                if self._samples and self._samples[-1][0] == sample[0]:
                    self._samples.pop()
                if self._play_t is not None and self._play_t > sample[0]:
                    self._play_t = sample[0]
                    self._last_wall = None     # its second starts now
            self._samples.append(sample)

    def set_rate(self, sim_s_per_wall_s: float) -> None:
        """Playback rate: 1.0 is real time, 25.0 is the dashboard's default speed."""
        with self._lock:
            self._rate = max(0.01, float(sim_s_per_wall_s))

    def set_text(self, rows: Sequence[str]) -> None:
        """Up to four short lines for the node's OLED (sent when they change)."""
        with self._lock:
            for i in range(TEXT_ROWS):
                self._text[i] = str(rows[i]) if i < len(rows) else ""

    def take_events(self) -> List[UplinkEvent]:
        """Everything the board reported since the last call, oldest first."""
        out: List[UplinkEvent] = []
        while True:
            try:
                out.append(self._events.popleft())
            except IndexError:
                return out

    # ------------------------------------------------------------------ #
    # one iteration of the background thread (also called directly by tests)
    # ------------------------------------------------------------------ #
    def step(self, now: Optional[float] = None) -> None:
        self._maybe_reconnect(self._clock() if now is None else now)
        now = self._clock() if now is None else now      # opening a port takes seconds
        with self._lock:
            show = self._advance(now)
            text = list(self._text)
        with self._io:
            if show is not None:
                self.bridge.publish(show)      # dedups; resends once a second as heartbeat
            row = self.bridge.text_pending(text)
            if row is not None:                # one line per step keeps the board's
                self.bridge.send_text(row, text[row])   # 64-byte receive buffer happy
            events = self.bridge.poll()
        self._events.extend(events)

    def _advance(self, now: float) -> Optional[Aspects]:
        """Move the playhead and return the aspects to show (``None``: nothing yet)."""
        # a stall (a port being opened, a busy machine) resumes where it stopped instead of
        # skipping the seconds it missed: every amber gets its full length
        wall_dt = 0.0 if self._last_wall is None else min(MAX_STEP_S, max(0.0, now - self._last_wall))
        self._last_wall = now
        if self._samples and self._play_t is None:
            self._play_t = self._samples[0][0]          # first data: show it at once
        if self._play_t is None:
            return self._cur[1] if self._cur is not None else None

        self._play_t += wall_dt * self._rate
        newest = self._samples[-1][0] if self._samples else self._cur[0]
        # never run past the end of the newest known second: when the next decision's
        # seconds arrive they must start playing immediately, not be skipped
        self._play_t = min(self._play_t, newest + 1.0)
        # too far behind (the speed was just lowered, or the thread stalled): skip ahead,
        # keeping one decision interval of backlog
        max_lag = max(2.0 * self.decision_s, 0.6 * self._rate)
        if newest - self._play_t > max_lag:
            target = newest - self.decision_s
            self.skipped += int(target - self._play_t)
            self._play_t = target
        while self._samples and self._samples[0][0] <= self._play_t + 1e-6:
            self._cur = self._samples.popleft()
        return self._cur[1] if self._cur is not None else None

    def _maybe_reconnect(self, now: float) -> None:
        """(Re)open the port while it is closed. Runs without holding ``_io``: opening a
        serial port waits for the board to boot (about two seconds)."""
        if self.bridge.link_alive or self._reopen is None or now < self._next_reopen:
            return
        self._next_reopen = now + self._reconnect_s
        try:
            link = self._reopen()
        except Exception as exc:               # HardwareUnavailable, or anything odd
            self._set_error(str(exc))
            return
        if not link.connected:
            return
        with self._io:
            self.bridge.replace_link(link)
        self.reconnects += 1
        self._set_error(None)
        log.info("hardware: connected to %s", link.describe())

    def _set_error(self, message: Optional[str]) -> None:
        if message != self.last_error:
            self.last_error = message
            if message:
                log.warning("hardware: %s (retrying every %.0f s)", message, self._reconnect_s)

    # ------------------------------------------------------------------ #
    # thread
    # ------------------------------------------------------------------ #
    def start(self) -> "HardwareMirror":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="atsc-hardware", daemon=True)
            self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:                  # pragma: no cover - defensive
                pass
            self._stop.wait(self._poll_s)

    def close(self) -> None:
        """Stop the thread, leave the board all-red, release the port."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._io:
            self.bridge.close()

    # ------------------------------------------------------------------ #
    # status
    # ------------------------------------------------------------------ #
    def state(self) -> int:
        """0 = port closed or failed, 1 = port open but the node is silent, 2 = live.

        Reads two attributes without locking, so a slow serial write can never hold up
        the dashboard tick that asks."""
        if not self.bridge.link_alive:
            return 0
        seen = self.bridge.node_last_seen_s
        return 2 if seen is not None and seen <= LIVE_WITHIN_S else 1

    def lag_s(self) -> float:
        """How far (simulated seconds) the lamps trail the newest simulated second."""
        with self._lock:
            if self._play_t is None:
                return 0.0
            newest = self._samples[-1][0] if self._samples else (
                self._cur[0] if self._cur is not None else self._play_t)
            return max(0.0, newest - self._play_t)

    def snapshot(self) -> Dict[str, object]:
        snap = self.bridge.snapshot()
        snap.update({"state": ("offline", "silent", "live")[self.state()],
                     "lag_s": round(self.lag_s(), 1), "rate": round(self._rate, 3),
                     "reconnects": self.reconnects, "skipped_s": self.skipped,
                     "error": self.last_error})
        return snap
