"""The bridge that ties the simulation to the hardware node.

Responsibilities, kept deliberately narrow:

* turn each intersection's FSM state into one idempotent lamp frame, and send it **only
  when the board's appearance actually changes** (plus a slow heartbeat so a node that
  booted late still catches up, and so the node's link watchdog stays fed);
* send short text lines for the node's OLED, again only when they change;
* read the uplink, throw away noise and anything referring to an intersection or
  approach that does not exist in this topology, and hand the survivors back as events;
* never raise into the caller. A pulled cable, a wedged port or a firmware crash degrades
  the demo to software-only and is visible in :attr:`stats`.

Why dedup matters: the dashboard advances several simulated seconds per wall tick, so a
naive implementation would emit a frame per simulated second per intersection. The lamps
can only ever show one appearance, so the change filter cuts the traffic by roughly an
order of magnitude and keeps the serial line quiet enough to read by eye.

This module deliberately imports nothing from :mod:`atsc.envs` or :mod:`atsc.sim` — the
hardware layer is a leaf, so it can be tested standalone and cannot create an import
cycle. Callers pass plain ``(state_value, current_phase)`` tuples. It is not thread-safe:
:class:`~atsc.hw.mirror.HardwareMirror` owns it from a single thread.
"""
from __future__ import annotations

import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from atsc.hw.link import Link, NullLink
from atsc.hw.protocol import (
    TEXT_ROWS,
    UplinkEvent,
    decode_uplink,
    encode_lamps,
    encode_text,
    sanitize_text,
)


class HardwareBridge:
    """Couples a :class:`~atsc.hw.link.Link` to the controller's signal and sensor paths."""

    def __init__(
        self,
        link: Link,
        n_phases: int,
        valid_tls: Sequence[str],
        heartbeat_s: float = 1.0,
        detect_cooldown_s: float = 0.0,
        clock=time.monotonic,
    ) -> None:
        self.link = link
        self.n_phases = int(n_phases)
        self._valid_tls = set(valid_tls)
        self._n_tls = len(valid_tls)
        self._heartbeat_s = max(0.0, float(heartbeat_s))
        self._cooldown_s = max(0.0, float(detect_cooldown_s))
        self._clock = clock

        self._last_aspects: Optional[str] = None
        self._last_send = 0.0
        self._last_detect: Dict[Tuple[str, str], float] = {}
        self._text_sent: List[Optional[str]] = [None] * TEXT_ROWS

        self.stats: Dict[str, float] = {
            "frames_sent": 0, "frames_suppressed": 0, "write_failures": 0, "text_sent": 0,
            "lines_read": 0, "lines_rejected": 0,
            "detections": 0, "queue_reports": 0, "emergencies": 0, "controls": 0,
            "heartbeats": 0, "cooldown_drops": 0, "unknown_tls": 0,
        }
        self._node_uptime_ms = 0
        self._node_seen_at: Optional[float] = None
        self.node_info: Optional[str] = None

    # ------------------------------------------------------------------ #
    # lamps and text (downlink)
    # ------------------------------------------------------------------ #
    def publish(self, per_intersection: Iterable[Tuple[str, int]]) -> bool:
        """Send the lamp frame for the current signal states; ``True`` if a frame went out.

        Feed it every simulated second (the amber and all-red last 3 s and 2 s inside a
        5 s decision interval) and call it again at least once per ``heartbeat_s`` even
        when nothing changed: the node goes to flashing amber after 3 s of silence.
        """
        try:
            frame = encode_lamps(per_intersection, self.n_phases)
        except ValueError:
            self.stats["lines_rejected"] += 1
            return False

        aspects = frame.decode("ascii")[3:].split(",")[0]
        now = self._clock()
        unchanged = aspects == self._last_aspects
        if unchanged and (self._heartbeat_s <= 0 or now - self._last_send < self._heartbeat_s):
            self.stats["frames_suppressed"] += 1
            return False

        if self.link.write(frame):
            self.stats["frames_sent"] += 1
            self._last_aspects = aspects
            self._last_send = now
            return True
        self.stats["write_failures"] += 1
        return False

    def send_text(self, row: int, text: str) -> bool:
        """Show ``text`` on row ``row`` (0-3) of the node's OLED; sent only when it changed."""
        if not 0 <= row < TEXT_ROWS:
            return False
        text = sanitize_text(text)
        if self._text_sent[row] == text:
            return False
        if self.link.write(encode_text(row, text)):
            self._text_sent[row] = text
            self.stats["text_sent"] += 1
            return True
        self.stats["write_failures"] += 1
        return False

    def text_pending(self, rows: Sequence[Optional[str]]) -> Optional[int]:
        """The first row of ``rows`` that differs from what the node shows, or ``None``.
        A row given as ``None`` has nothing to show yet and is skipped."""
        for i, text in enumerate(rows[:TEXT_ROWS]):
            if text is not None and self._text_sent[i] != sanitize_text(text):
                return i
        return None

    @property
    def last_aspects(self) -> Optional[str]:
        """The aspect string most recently sent, e.g. ``'GRARRRRG'``."""
        return self._last_aspects

    def forget_sent(self) -> None:
        """Assume the node shows nothing: the next lamp frame and every text row go out
        again. Called when the node (re)announces itself and after a reconnect."""
        self._last_aspects = None
        self._text_sent = [None] * TEXT_ROWS

    def replace_link(self, link: Link) -> None:
        """Swap in a freshly opened transport (after the old one failed)."""
        try:
            self.link.close()
        except Exception:
            pass
        self.link = link
        self.forget_sent()

    # ------------------------------------------------------------------ #
    # sensors (uplink)
    # ------------------------------------------------------------------ #
    def poll(self) -> List[UplinkEvent]:
        """Drain the uplink and return the events the simulation should act on.

        Heartbeats and the node's identity line are consumed here (they only update
        liveness), and detections for an intersection outside this topology are dropped —
        a mis-set ``tls`` in the firmware must not be able to inject phantom traffic.
        """
        events: List[UplinkEvent] = []
        for line in self.link.read_lines():
            if not line.strip():
                continue
            self.stats["lines_read"] += 1
            event = decode_uplink(line)
            if event is None:
                self.stats["lines_rejected"] += 1
                continue
            now = self._clock()

            if event.kind == "heartbeat":
                self.stats["heartbeats"] += 1
                self._node_uptime_ms = event.uptime_ms
                self._node_seen_at = now
                continue
            if event.kind == "info":
                # the node just booted, or just heard us again: it shows nothing of ours
                self.node_info = event.text
                self._node_seen_at = now
                self.forget_sent()
                continue

            if event.kind in ("detect", "queue"):
                if event.tls not in self._valid_tls:
                    self.stats["unknown_tls"] += 1
                    continue
                if event.kind == "detect":
                    if self._cooldown_s > 0:
                        key = (event.tls, event.approach)
                        last = self._last_detect.get(key)
                        if last is not None and now - last < self._cooldown_s:
                            self.stats["cooldown_drops"] += 1
                            continue
                        self._last_detect[key] = now
                    self.stats["detections"] += 1
                else:
                    self.stats["queue_reports"] += 1
            elif event.kind == "emergency":
                self.stats["emergencies"] += 1
            elif event.kind == "control":
                self.stats["controls"] += 1

            events.append(event)
        return events

    # ------------------------------------------------------------------ #
    # liveness, reporting, teardown
    # ------------------------------------------------------------------ #
    @property
    def link_alive(self) -> bool:
        """False once the transport has failed (cable pulled) or been closed."""
        return self.link.connected

    @property
    def node_last_seen_s(self) -> Optional[float]:
        """Seconds since the node last spoke, or ``None`` if it never has."""
        if self._node_seen_at is None:
            return None
        return max(0.0, self._clock() - self._node_seen_at)

    def snapshot(self) -> Dict[str, object]:
        """Small JSON-safe status block for the dashboard and the ``doctor`` command."""
        seen = self.node_last_seen_s
        return {
            "transport": self.link.describe(),
            "connected": self.link_alive,
            "node": self.node_info,
            "aspects": self._last_aspects,
            "node_uptime_ms": self._node_uptime_ms,
            "node_last_seen_s": None if seen is None else round(seen, 2),
            "stats": dict(self.stats),
        }

    def describe(self) -> str:
        alive = "connected" if self.link_alive else "offline"
        return (f"{self.link.describe()} ({alive}); sent {int(self.stats['frames_sent'])}, "
                f"suppressed {int(self.stats['frames_suppressed'])}, "
                f"detections {int(self.stats['detections'])}, "
                f"emergencies {int(self.stats['emergencies'])}")

    def close(self) -> None:
        """Leave the board showing all-red if we still can, then release the port.

        All-red is held only as long as the node's link watchdog allows (3 s); after that
        the node falls back to flashing amber, as a signal without a controller should.
        """
        try:
            if self.link.connected and self._n_tls:
                dark = [("all_red", 0)] * self._n_tls
                self.link.write(encode_lamps(dark, self.n_phases))
        except Exception:
            pass
        try:
            self.link.close()
        except Exception:
            pass

    @classmethod
    def disabled(cls, n_phases: int = 2) -> "HardwareBridge":
        """A bridge that goes nowhere — what the session holds when hardware is off."""
        return cls(NullLink(), n_phases, ())
