"""Safety tests for the signal FSM - the property reviewers care about most.

Under thousands of random phase requests, the FSM must never:
  * hold a green shorter than min_green (excluding a run-truncated final green),
  * hold a green longer than max_green,
  * switch between two different greens without a full yellow + all-red clearance
    (so conflicting movements can never be green at the same time).
"""
import numpy as np

from atsc.envs.phases import SignalFSM, SignalState
from atsc.sim.backend import _phase_set


def _run_random(seed, steps=6000, min_g=10, max_g=60, yellow=3, all_red=2):
    phases = _phase_set("ns_ew")
    fsm = SignalFSM(phases, min_g, max_g, yellow, all_red)
    rng = np.random.default_rng(seed)
    history = []
    for t in range(steps):
        if rng.random() < 0.3:
            fsm.request(int(rng.integers(0, len(phases))))
        greens = fsm.tick()
        history.append((t, fsm.state, fsm.current, tuple(greens)))
    return history


def _green_segments(history):
    segs, cur, start = [], None, None
    for t, state, phase, greens in history:
        key = phase if state == SignalState.GREEN else None
        if key != cur:
            if cur is not None:
                segs.append((cur, start, t - 1))
            cur, start = key, t
    segs.append((cur, start, len(history) - 1))
    return [(p, s, e) for (p, s, e) in segs if p is not None]


def test_min_and_max_green_respected():
    for seed in range(3):
        segs = _green_segments(_run_random(seed))
        # The final green may be truncated by the end of the run (not a real switch),
        # so exclude it from the min-green bound; max-green still applies to all.
        last_end = segs[-1][2] if segs else -1
        for (_p, s, e) in segs:
            dur = e - s + 1
            if e < last_end:
                assert dur >= 10, f"green too short: {dur}s (seed {seed})"
            assert dur <= 60, f"green too long: {dur}s (seed {seed})"


def test_yellow_all_red_clearance_between_conflicting_greens():
    for seed in range(3):
        segs = _green_segments(_run_random(seed))
        for i in range(1, len(segs)):
            if segs[i][0] != segs[i - 1][0]:
                gap = segs[i][1] - segs[i - 1][2] - 1
                assert gap >= 3 + 2, f"insufficient clearance between greens: {gap}s"


def test_no_conflicting_greens_simultaneously():
    hist = _run_random(0)
    for _t, _state, _phase, greens in hist:
        g = set(greens)
        assert not (g & {"N", "S"} and g & {"E", "W"}), "conflicting greens active together"


def test_initial_green_holds_full_min_green():
    fsm = SignalFSM(_phase_set("ns_ew"), 10, 60, 3, 2)
    fsm.request(1)  # ask to switch away immediately
    greens = [bool(fsm.tick()) for _ in range(11)]
    # first 10 ticks must remain green (min-green), tick 11 (index 10) turns to yellow
    assert all(greens[:10]), "initial green ended before min-green"
    assert not greens[10], "initial green did not end promptly after min-green"


def test_request_out_of_range_raises():
    fsm = SignalFSM(_phase_set("ns_ew"), 10, 60, 3, 2)
    try:
        fsm.request(5)
        assert False, "expected ValueError"
    except ValueError:
        pass
