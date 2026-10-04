# Changelog

## 1.2.0 — 2026-10-04

### Unchanged on purpose

The RL model, the shipped checkpoint, the safety FSM (`src/atsc/envs/phases.py`) and every
published number. `python run.py eval` still regenerates `outputs/benchmark_results.csv` and
`outputs/benchmark_summary.csv` byte-identically; training and the benchmark never import
the hardware code. The hosted site is unaffected: hardware mode is off unless asked for, and
`requirements-deploy.txt` is unchanged.

### Added: the Arduino signal model (`docs/HARDWARE.md`)

- **Firmware** `firmware/atsc_signal_node/` for an Arduino Uno (or Nano): 16 signal heads
  through six chained 74HC595s, 8 IR sensors at J0_0 (arrival and queue), a 4-button 433 MHz
  remote (ambulance, police car, fire engine, pause/resume), an optional OLED with its own
  small text driver (no libraries to install) and a buzzer. Boot lamp test; **flashing amber
  after 3 s without the PC**; CRC-checked frames; debounced inputs; 9.5 KB flash, 1.2 KB RAM.
  Its logic lives in `atsc_core.h`, plain C++ that the tests compile on the PC.
- **`python run.py demo --hardware [--port P] [--mirror fixed] [--fast]`**: the dashboard
  drives the board. The lamps follow the RL grid (or the fixed-time one) **second by second
  in real time** — `atsc/hw/mirror.py` replays the per-second lamp states the simulation
  produces in bursts, so the 3 s amber and 2 s all-red last 3 s and 2 s on the model at any
  dashboard speed. A sensed car is added to **both** grids (the race stays fair) without
  drawing a random number (`MiniBackend.add_detected_arrival`), so the simulated traffic
  around it is unchanged; a car waiting on a queue sensor tops that queue up; the remote
  dispatches emergencies through the same validated path as the Inject buttons. The board
  is opened on a background thread and reopened every 2 s while missing, so it can be
  plugged in, unplugged and plugged back during a demo. The OLED shows live figures.
- **Real-time speed** in hardware mode, labelled *real time* / *N× real time* on the slider,
  and a status-bar pill: *signal board live · N cars sensed · N remote calls*. `/healthz`
  includes a `hardware` block.
- **`python run.py hwtest`**: finds the board, walks every head through green-amber-red
  junction by junction, then ticks off each of the 8 sensors and 4 buttons as you trigger them.
- `python run.py doctor` reports pyserial and any board plugged in; `pyserial==3.5` added to
  `requirements.txt` (local only).
- Protocol additions (`atsc/hw/protocol.py`): `>T` text lines for the OLED, `<E` with the
  vehicle kind, `<C` control (pause/resume), `<I` node identity.
- `docs/HARDWARE.md`: parts list, layout figure, wiring figures and the full table of which
  output drives which lamp, upload, first power-on, self-test, calibration, remote pairing,
  a five-minute viva demonstration, settings and troubleshooting. Figures are generated from
  the firmware's own numbering by `tools/hardware_diagrams.py`.
- `tools/virtual_board/`: the compiled firmware on a simulated ATmega328P (simavr) with a
  simulated shift-register chain, OLED, sensors and remote, connected over a pseudo-terminal
  to the real dashboard. Used to verify this release: every lamp change matched the RL grid,
  amber 3.0 s and all-red 2.0 s on the board's own clock, sensors and buttons reached both
  grids, hot-plug reconnected, worst-case serial bursts were all applied in order.
- 43 tests in `tests/test_hardware.py` (195 in total): protocol, the firmware logic compiled
  for the PC against the Python side (CRC, parser, lamp wiring, line resync, debounce, link
  watchdog), real-time playback with a fake clock, reconnects, the session in hardware mode,
  the RNG-invariance of sensed cars, hardware-off importing nothing, and the virtual board end
  to end (skipped where the tools are missing).

### Fixed / improved

- The browser's playback clock now moves on steadily between frames instead of catching up
  in jumps: on-screen vehicle speed varied by about ±30 % within every 200 ms frame interval
  before, now by under 2 %, at the same average lag (and the new real-time speed, with a
  frame only every 5 s, plays smoothly instead of in bursts).
- The speed slider showed the page's placeholder (1×) for up to a second after loading
  instead of the server's speed.

## 1.1.0 — 2026-10-01

### Unchanged on purpose

The RL model and its training code path, the shipped checkpoints
(`models/pretrained/*.pt`), the safety FSM (`src/atsc/envs/phases.py`) and every published
number are byte-for-byte the same. `python run.py eval` still regenerates
`outputs/benchmark_results.csv` and `outputs/benchmark_summary.csv` **byte-identically**
(checked after every simulator change in this release). The original 26 tests are untouched
and pass.

### Fixed

- **The hosted server used about half a CPU core while doing nothing.** NumPy's OpenBLAS
  keeps worker threads that spin-wait after every call, and the dashboard evaluates the
  network five times a second, so they never slept: about 47 % of a core with no viewers
  (measured), against the 0.1 CPU of a free Render instance, so the process was throttled
  all the time, delaying every tick and frame for every visitor. `asgi.py` and
  `run.py demo` now pin BLAS/OpenMP to one thread before NumPy loads (`atsc/threads.py`;
  an explicit `OPENBLAS_NUM_THREADS` still wins): **1.5 % idle, 17 % with 100 viewers**.
  Training and the benchmark are not affected. `/healthz` now reports `cpu_percent`, so
  this can be checked on the live instance.
- **The live site's WebSocket was refused (HTTP 403) on every connection**, so the page
  fell back to polling full state 4×/s. `server.py` uses `from __future__ import annotations`
  and imported `WebSocket` only inside `build_app()`; FastAPI could not resolve the string
  annotation and treated the socket as a required query parameter. `WebSocket` is now
  imported at module level (guarded for the stdlib fallback), and a regression test checks
  the route has no query parameters. The bottom bar now reads **live · streaming**.
- A single bad `scenario` command (e.g. a typo) froze the shared race for every visitor at
  the next reset. Every command is now validated when it arrives and refused with a reason
  (`400` / an `ack` with `error`); the simulation only changes inside a tick.
- The stdlib fallback server could serve any file on the machine (`/static/../../config.yaml`,
  `/static//etc/os-release`). One path guard (`dashboard/assets.py:resolve_static`) now serves
  only real files inside `static/` for both servers.
- `run.py eval` crashed after writing the CSVs when `tabulate` was missing, so the summary
  and plots were never rebuilt. The markdown summary is now written without it.
- The dashboard ignored a `--quick` checkpoint and ran an untrained policy.
- Turning on QMIX crashed training at the end of the first episode, and the shipped
  checkpoint failed to load with an obscure error; documented settings such as
  `rl.share_parameters: false` / `rl.neighbor_obs: false` failed the same way. Checkpoints are
  now checked against `config.yaml` on load and any mismatch is reported in one readable
  message with the fix; QMIX trains, saves and reloads (tested).
- Installing SUMO silently moved `run.py eval` onto SUMO (the config's `backend: auto`), so
  a rerun no longer reproduced the published table. `eval.backend: mini` pins the benchmark;
  `--backend sumo` writes to `outputs/sumo/` instead of over the published files.
- `run.py train` (full) overwrote the shipped model and `outputs/logs/train.csv` without
  warning, and `train --quick` overwrote `outputs/training_curve.png`. A full run now needs
  `--overwrite`; the quick curve goes to `training_curve_quick.png`.
- Max-pressure read the wrong downstream queue for `phase_scheme: quad` (identical results
  for the shipped `ns_ew`).
- `eval.episode_seconds` was ignored; it now sets the benchmark episode length (same value,
  same numbers).
- SUMO: an injected emergency vehicle now gets a full, connected corridor route.
- `run.sh` is executable again (mode 755; see the push steps for Windows checkouts).
- "SUMO not detected" was logged once per episode (36× per benchmark); now once per process.

### Dashboard (rewritten front end, new wire protocol)

- **Compact protocol (v2):** `hello` once, then a keyframe every 10 s or after a reset and
  deltas in between, carrying only what changed plus events (vehicle left a queue / left the
  grid). Payload per tick: **~0.7 KB** at the default medium 1× (was 8.4 KiB), **4.1 KB** at
  rush 8× (was 11.0 KiB). Server tick (simulate + encode, same machine, back to back):
  **1.6 ms** medium 1× (was 2.2 ms), **12.2 ms** rush 8× (was 15.1 ms) — see
  `tools/bench_server.py`.
- **Exact between-frame picture:** the browser reconstructs every queue, every vehicle on a
  link and every vehicle crossing a junction for any instant between two frames; a test
  proves it equals the simulator's state second by second, and another that the JavaScript
  model equals its Python mirror on a recorded stream.
- **Connection:** exponential back-off with jitter, heartbeat + watchdog + ping, per-viewer
  outbox (a slow phone is resynced with a keyframe instead of building a backlog), command
  acknowledgements with readable errors, polling only as a last resort, hidden tabs
  disconnect after a minute, `max_clients` with a polite `busy`.
- **"Waking up the server…"**: a network-first service worker lets returning visitors see a
  friendly wake screen while a sleeping free instance boots; the page reconnects by itself
  and reloads once if the server was redeployed.
- **Rendering:** three stacked canvases (static roads painted once, moving layer redrawn per
  frame, edge fade on top), everything drawn in `requestAnimationFrame` with interpolation,
  sprites pre-rendered per kind/colour/scale, redraw skipped while paused, pause when the tab
  is hidden. Desktop frame rate in headless Chromium at rush hour **58 fps** (was 41–43,
  with jank), **25 fps** at a 4× CPU slowdown (was 14).
- **Indian traffic:** 18 vehicle types drawn in code — motorcycle, scooter, bicycle, cycle
  rickshaw, auto-rickshaw, e-rickshaw, car, taxi, SUV, van, tempo, truck, tractor with
  trolley, city bus, school bus, ambulance, police car, fire engine — larger and to scale,
  left-hand traffic, slow vehicles in the kerb lane, two-wheelers pairing up, four colour
  variants each. The mix is configurable (`traffic_mix`) and cosmetic: kinds come from their
  own random stream, so the traffic and every number are unchanged.
- **Emergency vehicles:** ambulance, police car and fire engine (`emergency.types`), each
  with flashing beacons, an Inject button, a banner naming the vehicle and the pre-empted
  junction, a pulsing ring on that junction, and a clearance-time KPI. The banner follows the
  vehicle on both grids ("through the RL grid — still in fixed-time traffic"), and the KPI's
  "N× faster" compares only vehicles that have cleared *both* grids, so it never sets one
  grid's three ambulances against the other's two. Pre-emption goes through the existing
  `control/emergency.py` and the safety FSM.
- **Layout and accessibility:** both grids are entirely on the first screen on laptops and
  desktops (checked at 1440×900, 1366×657 and 1280×720), with the KPI cards in a column
  beside them; the emergency banner heads that column, so it never pushes the grids down.
  Narrower windows and tablets put the race first and the numbers under it; phones show the
  RL grid on the first screen. No horizontal scroll anywhere. Every form field labelled (the
  four "No label associated with a form field" issues are gone), strict same-origin CSP,
  reduced motion respected, status announcements for screen readers.
- **KPI signs:** the cards show RL's change against fixed-time with the README's convention
  (−26.5 % = 26.5 % less waiting); `benchmark_summary.md` now uses the same signed form.
- Code split into ES modules: `transport`, `model`, `renderer`, `sprites`, `charts`, `ui`,
  `main`; the 696-line `app.js` is gone.

### Deployment

- `requirements-deploy.txt` pins **all 38** runtime packages (transitive included);
  `websockets==13.1` matches uvicorn 0.30.1's server API without deprecation warnings.
  Verified with a real `pip install` on Python 3.12 (binary wheels only).
- `render.yaml`, `Procfile`, `Dockerfile`: `--ws-max-size 65536 --ws-ping-interval 20
  --ws-ping-timeout 20`; health check on the new cheap `/healthz`; Dockerfile on
  `python:3.12.6-slim` with `HEALTHCHECK`, `exec` so uvicorn gets signals, binary wheels only.
- `.dockerignore` keeps the dashboard's front-end files explicitly and leaves out `tools/`.
- `/healthz` reports version, build id, viewers, tick cost, payload size, CPU and memory.
- `/` and `/healthz` answer `HEAD` as well as `GET` (FastAPI answered `405`, which uptime
  monitors that probe with `HEAD` report as "down").
- Deploy stack (Python 3.12.6): ~76 MiB and 1.5 % of a core idle, ~101 MiB and 17 % with
  100 viewers (512 MiB and 0.1 CPU available).

### Tooling

- `tools/e2e_browser.py`: headless-Chromium check of a local or deployed site — the
  server's `/healthz` (no failed ticks, CPU not saturated), live status, console and DevTools
  issues, layout on desktop, two laptop sizes and a phone (grids entirely on the first
  screen, no sideways scroll), every Inject button, every vehicle type, controls, frame rate,
  and (locally) the wake screen.
- `tools/bench_server.py`: per-tick cost and payload, or a load test with N viewers.
- `run.sh` / `run.bat` pick a supported Python (3.10–3.12) and explain what to do with 3.13,
  recreate a `.venv` made by an unsupported Python, and reinstall when `requirements.txt`
  changes. `run.py --debug` prints full tracebacks.
- `.gitattributes` keeps `run.sh` LF and `run.bat` CRLF on every platform, and marks the
  published CSVs `-text` so git never rewrites their line endings (`outputs/logs/train.csv`
  is CRLF and stays byte-identical).

### Tests

152 tests (was 26): `test_dashboard_session.py`, `test_dashboard_ws.py` (incl. a real
uvicorn + websockets connection), `test_dashboard_model_js.py` (Node), `test_stdlib_server.py`,
`test_static_assets.py`, `test_vehicles.py`, `test_agents_compat.py`, `test_threads.py`;
helper `tests/dashboard_model.py`.

### Docs

README (dashboard, protocol, deploy numbers, troubleshooting, a Known limitations section),
`docs/DEPLOYMENT.md` (rewritten: the 403 post-mortem, a post-deploy checklist, what to do if the
bar does not say "live · streaming"), corrections in REPORT, CHEATSHEET, EXPLAINER and
VIVA_MASTER (max-green is not a starvation guarantee; max-pressure wins at low/medium;
3 seeds, 112 episodes; no green-wave indicator), new dashboard screenshots.

### Known issues (unchanged, documented in README → Known limitations)

The FSM accepts requests during amber/all-red (mid-clearance cancellations; side streets
waited up to ~113 s); greens after the first last one second longer than configured; the
trainer treats the time limit as terminal; retraining is not reproducible across NumPy
versions; pre-emption reacts at the stop line. Each would change the published results.

### Files

| File | Change | Why |
|---|---|---|
| `src/atsc/dashboard/server.py` | rewritten | WebSocket 403 fix; per-viewer outboxes, heartbeat, backlog resync, acks, rate limits, `/healthz`, `/api/hello`, `/sw.js`, CSP headers, `HEAD` on `/` and `/healthz` |
| `src/atsc/dashboard/session.py` | rewritten | validated command queue, protocol v2 keyframes/deltas/events, live KPIs (emergency clearance paired across the grids), episode counter, quick-checkpoint fallback |
| `src/atsc/dashboard/stdlib_server.py` | rewritten | path-traversal fix, validation, size/rate limits, polling page |
| `src/atsc/dashboard/assets.py` | new | static-file guard, build id, page/service-worker templating |
| `src/atsc/dashboard/runtime.py` | new | JSON encoding, tick statistics, process CPU and memory, rate limiting, static responses, page headers |
| `src/atsc/dashboard/__init__.py` | edited | exports `CommandError` |
| `src/atsc/dashboard/static/index.html` | rewritten | labelled controls, emergency buttons, race before numbers, legend, wake screen, accurate description, CSP-compatible |
| `src/atsc/dashboard/static/styles.css` | rewritten | responsive layout: grids sized to the first screen, KPI column beside them on wide screens, race first on narrow ones; sticky bar fix, reduced motion |
| `src/atsc/dashboard/static/js/*.js` | new (7) | `main`, `transport`, `model`, `renderer`, `sprites`, `charts`, `ui` |
| `src/atsc/dashboard/static/sw.js` | new | network-first service worker for the wake screen |
| `src/atsc/dashboard/static/icon.svg` | new | favicon (no 404 in the console) |
| `src/atsc/dashboard/static/app.js` | removed | replaced by the modules above |
| `src/atsc/vehicles.py` | new | vehicle catalogue, traffic mix, emergency types and their validation |
| `src/atsc/threads.py` | new | one BLAS/OpenMP thread for the dashboard process (the spinning-thread CPU fix) |
| `src/atsc/sim/mini_backend.py` | edited | vehicle kinds from the mix (own RNG stream, faster sampling), read-only observer hooks and views for the dashboard, emergency kinds — traffic unchanged |
| `src/atsc/sim/backend.py` | edited | `inject_emergency(kind=)`, `wait_totals()`, docstrings |
| `src/atsc/sim/sumo_backend.py` | edited | emergency kinds, full corridor route, unused import |
| `src/atsc/sim/netgen.py` | edited | `straight_corridor_edges()`, docstring |
| `src/atsc/sim/__init__.py` | edited | SUMO fallback warning once per process |
| `src/atsc/envs/traffic_env.py` | edited | `inject_emergency(kind=)`, docstrings (FSM and reward untouched) |
| `src/atsc/control/max_pressure.py` | edited | correct downstream approach for `quad` |
| `src/atsc/control/emergency.py` | edited | accurate docstring, unused import |
| `src/atsc/agents/multi_agent.py` | edited | checkpoint/config compatibility check on load |
| `src/atsc/agents/qmix.py` | edited | float `epsilon`, readable load errors, accurate docstring |
| `src/atsc/agents/__init__.py` | edited | readable error when QMIX is enabled without PyTorch |
| `src/atsc/agents/net.py`, `hw/__init__.py`, `hw/link.py` | edited | unused imports only |
| `src/atsc/eval/benchmark.py` | edited | `eval.backend`, markdown summary without `tabulate`, signed changes, scenario order |
| `src/atsc/eval/metrics.py` | edited | `eval.episode_seconds` honoured |
| `src/atsc/eval/plots.py` | edited | training-curve file name parameter |
| `src/atsc/eval/__init__.py` | edited | lazy exports (no pandas/matplotlib for `atsc.eval.metrics`) |
| `src/atsc/train/trainer.py` | edited | honest docstring, unused imports (training logic untouched) |
| `src/atsc/config.py` | edited | validation of `traffic_mix`, `emergency.types`, `dashboard`, `eval.backend` |
| `src/atsc/__init__.py` | edited | version 1.1.0 |
| `config.yaml` | edited | `traffic_mix`, `emergency.types`, dashboard speeds/limits, `eval.backend`, accurate comments |
| `run.py` | edited | `train --overwrite`, `eval --backend`, `--debug`, quick curve name, version in `doctor`, one BLAS thread for `demo` |
| `run.sh`, `run.bat` | rewritten | Python 3.10–3.12 selection, requirements change detection; `run.sh` mode 755, `run.bat` CRLF |
| `asgi.py` | edited | one BLAS thread before NumPy loads, start command flags in the docstring, bad `ATSC_SCENARIO` tolerated, build id in the log |
| `requirements-deploy.txt` | rewritten | all 38 packages pinned |
| `render.yaml`, `Procfile`, `Dockerfile`, `.dockerignore` | edited | WebSocket flags, `/healthz`, pinned base image, health check, build context |
| `.gitignore`, `.gitattributes` | edited / new | throwaway outputs ignored; line endings per script type, published CSVs left byte-for-byte |
| `outputs/benchmark_summary.md` | regenerated | signed changes, throughput and clearance tables; same numbers (the CSVs are byte-identical) |
| `docs/screenshots/network_render_*.png` | regenerated | the new dashboard |
| `docs/screenshots/dashboard_desktop.png`, `dashboard_phone.png` | new | README stills |
| `README.md`, `docs/DEPLOYMENT.md`, `REPORT.md`, `CHEATSHEET.md`, `EXPLAINER.md`, `docs/VIVA_MASTER.md`, `docs/screenshots/README.md` | edited | see Docs above |
| `CHANGELOG.md` | new | this file |
| `tests/*.py` (8 test files + `dashboard_model.py`) | new | see Tests above |
| `tools/e2e_browser.py`, `tools/bench_server.py` | new | see Tooling above |

## 1.0.0

Initial release: multi-agent Double + Dueling DQN on a 2×2 grid, safety FSM, emergency
pre-emption, 36-run benchmark, live dashboard, 26 tests.
