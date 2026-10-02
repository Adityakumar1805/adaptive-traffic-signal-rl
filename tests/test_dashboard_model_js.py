"""The browser's model.js must reconstruct exactly what the Python reference does.

One recorded stream (hello + frames, with commands, speed changes and emergency vehicles) is
replayed through ``static/js/model.js`` in Node and through ``tests/dashboard_model.py``; the
keyframe-comparable state and the between-frames picture (``display(vt)``) must be identical.
Skipped when Node.js is not installed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from atsc.dashboard.runtime import encode
from atsc.dashboard.session import DashboardSession

from dashboard_model import Model

ROOT = Path(__file__).resolve().parents[1]
MODEL_JS = ROOT / "src" / "atsc" / "dashboard" / "static" / "js" / "model.js"
NODE = shutil.which("node")

RUNNER = r"""
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const [modelPath, streamPath] = process.argv.slice(2);
const { Model } = await import(pathToFileURL(modelPath).href);
const stream = JSON.parse(readFileSync(streamPath, "utf8"));
const model = new Model();
const out = [];
for (const step of stream) {
  model.apply(step.msg);
  for (const vt of step.samples) {
    const sides = {};
    for (const name of ["rl", "ft"]) {
      const d = model.sides[name].display(vt);
      sides[name] = {
        queues: d.queues.map((q) => ({ codes: q.codes, total: q.total, abs0: q.abs0, front_ids: q.front_ids })),
        movers: d.movers, crossing: d.crossing, exiting: d.exiting,
        s: model.sides[name].s, q: model.sides[name].q, qk: model.sides[name].qk,
        m: model.sides[name].m, pr: model.sides[name].pr, dep: model.sides[name].dep,
      };
    }
    // copy now: the model keeps mutating these arrays as later frames arrive
    out.push(JSON.parse(JSON.stringify({ vt, bt: model.bt, g: model.g, h: model.h, sides })));
  }
}
process.stdout.write(JSON.stringify(out));
"""


def _record(tmp_path):
    raw = (ROOT / "config.yaml").read_text(encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(raw.replace("keyframe_every_s: 10", "keyframe_every_s: 3600"), encoding="utf-8")
    s = DashboardSession(str(cfg))
    s.play()
    script = {4: {"a": "inject", "kind": "ambulance"}, 10: {"a": "speed", "v": 2},
              16: {"a": "inject", "kind": "fire", "corridor": "ns"}, 22: {"a": "speed", "v": 8},
              30: {"a": "speed", "v": 0.5}, 40: {"a": "inject", "kind": "police"},
              46: {"a": "scenario", "v": "rush"}, 58: {"a": "speed", "v": 1}}
    stream = [{"msg": json.loads(encode(s.hello())), "samples": []}]
    prev_bt = None
    for tick in range(70):
        if tick in script:
            s.submit(script[tick])
        if tick == 35:
            s.request_keyframe()
        frame = s.tick()
        if frame is None:
            continue
        msg = json.loads(encode(frame))
        bt = float(msg.get("bt", prev_bt or 0.0))
        lo = prev_bt if (prev_bt is not None and prev_bt <= bt) else bt
        samples = sorted({round(lo + (bt - lo) * f, 2) for f in (0.0, 0.13, 0.5, 0.77, 1.0)})
        stream.append({"msg": msg, "samples": samples})
        prev_bt = bt
    return stream


def _python(stream):
    model = Model()
    out = []
    for step in stream:
        model.apply(step["msg"])
        for vt in step["samples"]:
            sides = {}
            for name, side in model.sides.items():
                d = side.display(vt)
                sides[name] = {
                    "queues": [{k: q[k] for k in ("codes", "total", "abs0", "front_ids")}
                               for q in d["queues"]],
                    "movers": d["movers"], "crossing": d["crossing"], "exiting": d["exiting"],
                    "s": side.s, "q": side.q, "qk": side.qk, "m": side.m, "pr": side.pr,
                    "dep": side.dep,
                }
            # copy now: the model keeps mutating these lists as later frames arrive
            out.append(json.loads(json.dumps({"vt": vt, "bt": model.bt, "g": model.g,
                                              "h": model.h, "sides": sides})))
    return out


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_model_js_matches_the_python_reference(tmp_path):
    stream = _record(tmp_path)
    path = tmp_path / "stream.json"
    path.write_text(json.dumps(stream), encoding="utf-8")
    runner = tmp_path / "runner.mjs"
    runner.write_text(RUNNER, encoding="utf-8")
    res = subprocess.run([NODE, str(runner), str(MODEL_JS), str(path)], capture_output=True,
                         text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    js = json.loads(res.stdout)
    py = _python(stream)
    assert len(js) == len(py) > 200
    for a, b in zip(js, py):
        assert a == b, f"JS and Python disagree at vt={b['vt']}"
    # the stream really exercised the interesting paths
    assert any(x["sides"]["rl"]["crossing"] for x in py)
    assert any(x["sides"]["rl"]["exiting"] for x in py)
    assert any(any(q["front_ids"] for q in x["sides"]["rl"]["queues"]) for x in py)
