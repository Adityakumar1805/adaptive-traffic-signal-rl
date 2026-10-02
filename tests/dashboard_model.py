"""Reference implementation of the browser's frame handling (mirrors static/js/model.js).

Used by the tests to prove two things about the compact protocol:

* a viewer that applies ``hello`` + every delta ends up with exactly the state a fresh
  keyframe describes - nothing is lost (:meth:`Model.comparable`);
* the queue / link / junction picture the browser reconstructs for any moment *between* two
  frames (:meth:`Side.display`) is the simulator's real state at that moment - the browser
  never invents or drops a vehicle.

Keep the two implementations in step: ``tests/test_dashboard_model_js.py`` replays one
recorded stream through both and compares the results.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

# events stay this long (simulated seconds) after they stop mattering at the newest time, so
# a display clock that lags a frame behind (up to ~2 frames of 40 s at 8x) still finds them
KEEP_S = 150.0


class Side:
    def __init__(self, model: "Model") -> None:
        self.model = model
        n = model.n_approaches
        self.s = ""
        self.q: List[int] = [0] * n
        self.qk: List[str] = [""] * n
        # (id, t0*10) -> (kind, source approach, dest approach, t0*10)
        self.mv: Dict[Tuple[int, int], Tuple[int, int, int, int]] = {}
        # (id, t*10) -> (kind, source approach, exit dir, t*10)
        self.x: Dict[Tuple[int, int], Tuple[int, int, int, int]] = {}
        self.m: List[float] = []
        self.pr: List[int] = []
        self.dep: List[int] = [0] * n      # departures seen per source approach

    # -- applying frames ---------------------------------------------------- #
    def _adds(self, flat: List[int], bt10: int, count: bool) -> None:
        assert len(flat) % 5 == 0, "a / mv carry 5 integers per vehicle"
        for i in range(0, len(flat), 5):
            vid, kind, src, dest, dt0 = flat[i:i + 5]
            key = (vid, bt10 + dt0)
            if key in self.mv:
                continue
            self.mv[key] = (kind, src, dest, bt10 + dt0)
            if count:
                self.dep[src] += 1

    def _exits(self, flat: List[int], bt10: int) -> None:
        assert len(flat) % 5 == 0, "x carries 5 integers per vehicle"
        for i in range(0, len(flat), 5):
            vid, kind, src, direction, dt = flat[i:i + 5]
            key = (vid, bt10 + dt)
            if key in self.x:
                continue
            self.x[key] = (kind, src, direction, bt10 + dt)
            self.dep[src] += 1

    def _queues(self, d: Dict[str, Any]) -> None:
        if "q" in d:
            self.q = list(d["q"])
        for i, codes in d.get("qk", []):
            self.qk[i] = codes

    def keyframe(self, d: Dict[str, Any], bt10: int, new_episode: bool) -> None:
        if new_episode:
            n = self.model.n_approaches
            self.mv, self.x, self.dep = {}, {}, [0] * n
        self.s = d["s"]
        self.q = list(d["q"])
        self.qk = [""] * self.model.n_approaches
        self._queues({"qk": d["qk"]})
        self._adds(d.get("a", []), bt10, count=True)     # the tick's own events first ...
        self._exits(d.get("x", []), bt10)
        self._adds(d.get("mv", []), bt10, count=False)   # ... then merge the snapshot
        self.m = list(d["m"])
        self.pr = list(d["pr"])

    def delta(self, d: Dict[str, Any], bt10: int) -> None:
        if "s" in d:
            self.s = d["s"]
        self._queues(d)
        self._adds(d.get("a", []), bt10, count=True)
        self._exits(d.get("x", []), bt10)
        if "m" in d:
            self.m = list(d["m"])
        if "pr" in d:
            self.pr = list(d["pr"])

    def prune(self, bt: float) -> None:
        model = self.model
        self.mv = {k: v for k, v in self.mv.items() if bt < model.join_time(v[0], v[3]) + KEEP_S}
        self.x = {k: v for k, v in self.x.items() if bt < v[3] / 10.0 + KEEP_S}

    # -- views -------------------------------------------------------------- #
    def on_link(self, bt: float) -> Dict[Tuple[int, int], Tuple[int, int, int]]:
        """Vehicles on a link at simulator time ``bt``: ``t0 <= bt < t0 + travel``."""
        out = {}
        for key, (kind, src, dest, t0_10) in self.mv.items():
            t0 = t0_10 / 10.0
            if t0 <= bt < t0 + self.model.travel_of[kind]:
                out[key] = (kind, src, dest)
        return out

    def display(self, vt: float) -> Dict[str, Any]:
        """What the browser draws at display time ``vt`` (between the previous frame and the
        newest one, whose simulator time is ``model.bt``).

        Every vehicle is in exactly one place: queued at an approach (``vt < d``, d = the
        second its discharge starts), crossing the junction (``d <= vt < t0``), on a link
        (``t0 <= vt < J``, J = the second it joins the next queue) or queued again. Queues
        are the newest snapshot with the vehicles that joined after ``vt`` taken off the
        tail and the ones that left after ``vt`` put back at the head.
        """
        model = self.model
        step = model.step_s
        bt = model.bt
        n = model.n_approaches
        join: Dict[Tuple[int, int], float] = {}
        departs: Dict[Tuple[int, int], float] = {}
        for (vid, _k), (kind, src, dest, t0_10) in self.mv.items():
            join[(vid, dest)] = model.join_time(kind, t0_10)
            departs[(vid, src)] = t0_10 / 10.0 - step
        for (vid, _k), (kind, src, _dir, t_10) in self.x.items():
            departs[(vid, src)] = t_10 / 10.0 - step

        front: List[List[Tuple[float, int, int, int]]] = [[] for _ in range(n)]
        future_deps = [0] * n
        tail_remove = [0] * n
        movers: List[List[Tuple[float, int, int, int, float]]] = [[] for _ in range(n)]
        crossing: List[Tuple[int, int, int, int, float]] = []
        exiting: List[Tuple[int, int, int, int, float]] = []
        seq = 0
        for (vid, _k), (kind, src, dest, t0_10) in self.mv.items():
            seq += 1
            t0 = t0_10 / 10.0
            d = t0 - step
            jt = join[(vid, dest)]
            if vt < d:
                future_deps[src] += 1
                if join.get((vid, src), -math.inf) <= vt:
                    front[src].append((d, seq, vid, kind))
            elif vt < t0:
                crossing.append((vid, kind, src, dest, d))
            elif vt < jt and departs.get((vid, dest), math.inf) > vt:
                movers[dest].append((jt, vid, kind, src, t0))
            if vt < jt <= bt + 1e-9 and (vid, dest) not in departs:
                tail_remove[dest] += 1
        for (vid, _k), (kind, src, direction, t_10) in self.x.items():
            seq += 1
            t = t_10 / 10.0
            d = t - step
            if vt < d:
                future_deps[src] += 1
                if join.get((vid, src), -math.inf) <= vt:
                    front[src].append((d, seq, vid, kind))
            elif vt < t:
                crossing.append((vid, kind, src, -1 - direction, d))
            else:
                exiting.append((vid, kind, src, direction, t))

        queues = []
        for ai in range(n):
            f = sorted(front[ai])
            n_base = self.q[ai] - tail_remove[ai]
            assert n_base >= 0, (ai, self.q[ai], tail_remove[ai])
            base = [model.decode(c) for c in self.qk[ai][:max(0, min(len(self.qk[ai]), n_base))]]
            codes = [model.code(kind, vid) for _d, _s, vid, kind in f] + base
            queues.append({"codes": codes, "total": len(f) + n_base,
                           "abs0": self.dep[ai] - future_deps[ai],
                           "front_ids": [vid for _d, _s, vid, _k in f]})
        for lst in movers:
            lst.sort()
        return {"queues": queues, "movers": movers, "crossing": crossing, "exiting": exiting}


class Model:
    def __init__(self) -> None:
        self.hello: Dict[str, Any] = {}
        self.travel_of: Dict[int, float] = {}
        self.sides: Dict[str, Side] = {}
        self.g: Dict[str, Any] = {}
        self.h: List[List[float]] = []
        self.window = 120
        self.n_approaches = 0
        self.step_s = 1.0
        self.alphabet = ""
        self.nv = 1
        self.bt = 0.0
        self.ep: Optional[int] = None

    # -- helpers shared with Side ------------------------------------------ #
    def join_time(self, kind: int, t0_10: int) -> float:
        """When a vehicle that entered its link at ``t0`` joins the next queue: the backend
        takes ``remaining -= step`` each second and appends it once ``remaining <= 0``."""
        travel = self.travel_of[kind]
        return t0_10 / 10.0 + self.step_s * math.ceil(travel / self.step_s - 1e-6)

    def code(self, kind: int, vid: int) -> int:
        return kind * self.nv + vid % self.nv

    def decode(self, ch: str) -> int:
        return self.alphabet.index(ch)

    # -- frames ------------------------------------------------------------- #
    def apply(self, msg: Dict[str, Any]) -> None:
        t = msg.get("t")
        if t == "hello":
            self.hello = msg
            self.window = int(msg["win"])
            self.n_approaches = len(msg["grid"]["ids"]) * 4
            self.step_s = float(msg["step_s"])
            self.alphabet, self.nv = msg["qka"], int(msg["nv"])
            link, free = float(msg["grid"]["link"]), float(msg["free_mps"])
            self.travel_of = {i: link / (free * float(k[6])) for i, k in enumerate(msg["kinds"])}
            self.sides = {"rl": Side(self), "ft": Side(self)}
            self.g, self.h, self.ep = {}, [], None
            self.apply(msg["state"])
        elif t == "f":
            if "bt" in msg:
                self.g["bt"] = msg["bt"]
            self.bt = float(self.g.get("bt", 0.0))
            bt10 = int(round(self.bt * 10))
            if msg.get("kf"):
                new_episode = msg.get("ep") != self.ep
                self.ep = msg.get("ep")
                for name in ("rl", "ft"):
                    self.sides[name].keyframe(msg[name], bt10, new_episode)
                self.h = [list(p) for p in msg.get("h", [])]
            else:
                for name in ("rl", "ft"):
                    if name in msg:
                        self.sides[name].delta(msg[name], bt10)
                self.h.extend(list(p) for p in msg.get("h", []))
                self.h = self.h[-self.window:]
            for key in ("k", "e", "p", "sp", "sc"):
                if key in msg:
                    self.g[key] = msg[key]
            for side in self.sides.values():
                side.prune(self.bt)
        # hb / pong / ack / busy carry no state

    def comparable(self) -> Dict[str, Any]:
        """The state a keyframe also describes (moving set evaluated at the current time)."""
        bt = float(self.g.get("bt", 0.0))
        out: Dict[str, Any] = {key: self.g.get(key) for key in ("bt", "e", "p", "sp", "sc")}
        for name, side in self.sides.items():
            out[name] = {"s": side.s, "q": side.q, "qk": side.qk, "m": side.m, "pr": side.pr,
                         "mv": side.on_link(bt)}
        out["h"] = self.h
        return out


def keyframe_comparable(hello: Dict[str, Any], frame: Dict[str, Any]) -> Dict[str, Any]:
    """Comparable view of a fresh keyframe, interpreted with the same ``hello``."""
    m = Model()
    m.apply(dict(hello, state=frame))
    return m.comparable()
