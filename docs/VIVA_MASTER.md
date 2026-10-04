# VIVA_MASTER.md — verified forensic reference

**What this file is.** A gap-filling companion to the docs already in the repo. It contains
only what they do **not**: a file-by-file inventory with *used / unused / dead* labels, every
result recomputed from `outputs/*.csv`, the documentation-vs-code contradictions that were
found and fixed, honest weaknesses, ranked improvements, and a project-specific Q&A bank.

Read the others for the material they already cover well:
`README.md` (how to run) · `REPORT.md` (formal methodology) ·
`EXPLAINER.md` (long-form teaching) · `CHEATSHEET.md` (the one page to memorise).

> **Version note (1.1.0).** The inventory and line counts below describe release 1.0.0.
> Release 1.1.0 rewrote the live dashboard (compact WebSocket protocol, ES-module front end
> with 18 Indian vehicle types and three dispatchable emergency vehicles, wake screen),
> fixed the WebSocket 403 on the hosted site, added checkpoint/config validation, and grew
> the test suite from 26 to 152 — see [CHANGELOG.md](../CHANGELOG.md). The RL model, the
> shipped checkpoint, the safety FSM and every benchmark number are unchanged. Answers below
> that 1.1.0 made wrong have been corrected in place (look for **(1.1.0)**).
>
> **Version note (1.2.0).** Release 1.2.0 adds the Arduino signal model — firmware in
> `firmware/`, the PC side in `src/atsc/hw/`, the build guide in
> [docs/HARDWARE.md](HARDWARE.md) — and 43 hardware tests (195 in total). Section 16 below
> covers the questions it invites. Model, checkpoint, FSM and benchmark numbers: unchanged.

**Verification standard used here.** Every number below was produced by running this
repository's own code on this repository's own artefacts. Three checks back it up:

| Check | Result |
|---|---|
| Unit tests | **26 passed, 0 failed** |
| `python run.py doctor` | **OK** — config valid, checkpoint loads |
| Reproduce `outputs/benchmark_results.csv` from source + shipped checkpoint | **36 / 36 rows bit-exact** |

That third check is the strongest claim in this document and the one to make in the viva: the
results table is not a screenshot of a run someone remembers — it regenerates, to the last
decimal, in about four seconds from `models/pretrained/atsc_2x2.pt`.

Anything that could **not** be verified is labelled *"cannot be verified from this project"*
in place. Nothing here is estimated, rounded up, or borrowed from a paper.

---

## 1. What this project actually is (and is not)

**Is:** a working multi-agent deep-RL traffic-signal controller for a 2×2 grid, with a
safety layer, emergency preemption, two baselines, a four-density benchmark, a live
browser dashboard, and 26 unit tests. 47 Python files, 5,463 lines of Python, 959 lines of
front-end, zero front-end dependencies.

**Is not:** a new RL algorithm. The learner is Double + Dueling DQN with Prioritized
Experience Replay — all published techniques (2015–2016). Claiming novelty in the algorithm
is the fastest way to lose marks. The defensible claim is **integration and rigour**:
neighbour-aware multi-agent coordination, a safety FSM that makes unsafe states impossible
by construction, preemption, honest benchmarking against *two* baselines on byte-identical
traffic, and a dependency-optional design that runs on a locked-down lab PC.

**Also is not:** validated on real traffic data. Every vehicle in every number below came
from a Poisson arrival process in a simulator. Say so before you are asked.

---

## 2. Verified file inventory

Built by parsing every file's AST and resolving the import graph, not by reading names.
`Imported by` is the actual set of modules that import each file.

**Status labels** (the taxonomy asked for, collapsed into one column):

| Label | Meaning |
|---|---|
| **CORE** | implemented *and* executed in the shipped experiment |
| **CORE-alt** | the zero-dependency fallback half of a deliberate dual path; executed on this machine, and the *only* path if SUMO/PyTorch/FastAPI are absent |
| **OPT** | implemented, reachable only behind a config flag that ships **off** |
| **DEAD** | on no reachable path; imported by nothing that runs |
| **TEST / DOC / GEN** | test code / documentation / generated output |

### 2.1 Entry point and infrastructure

| File | LOC | Status | Purpose | Imported by |
|---|---:|---|---|---|
| `run.py` | 198 | CORE | the only entry point: `doctor · demo · train · eval · sim` subcommands (plots are written by `eval` and `train`) | — (CLI) |
| `src/atsc/__init__.py` | 22 | CORE | package marker, version | — |
| `src/atsc/config.py` | 182 | CORE | loads `config.yaml` into an attribute-access dict; `validate_config` raises friendly errors | 7 modules + tests |
| `src/atsc/logging_utils.py` | 83 | CORE | console + CSV logging | 8 modules |
| `src/atsc/seeding.py` | 54 | CORE | `seed_everything`, `derive_seed` (sha256 → 32 bits), `make_rng` for independent streams | `mini_backend`, `trainer` |

### 2.2 Simulation layer

| File | LOC | Status | Purpose | Imported by |
|---|---:|---|---|---|
| `sim/backend.py` | 321 | CORE | the `SimBackend` interface, `NetworkTopo`/`build_topology`, `MetricsAccumulator`, approach↔travel maps, `_phase_set` | 12 modules — the widest-used file in the repo |
| `sim/mini_backend.py` | 429 | **CORE-alt** | built-in point-queue simulator. **Produced every number in `outputs/`** because SUMO is not installed here | `sim/__init__`, tests |
| `sim/sumo_backend.py` | 424 | CORE (primary, unexercised here) | SUMO via libsumo/traci; maps approaches ↔ per-link `G/r` state strings | `sim/__init__`, `run.py` |
| `sim/netgen.py` | 273 | CORE (SUMO path only) | writes `grid.nod.xml`, `grid.edg.xml`, `.rou.xml`, `.sumocfg`; calls `netconvert --tls.guess` | `sim/sumo_backend.py` |
| `sim/sumo_detect.py` | 144 | CORE | cross-platform SUMO discovery; never raises | `sim/__init__`, `sumo_backend`, `run.py` |
| `sim/__init__.py` | 79 | CORE | `make_backend()` — the auto/sumo/mini selector | `traffic_env`, `run.py` |

**Honest note on the SUMO half.** `sumo_backend.py` + `netgen.py` are complete and are the
configured *primary* backend, but they cannot be executed in this environment (no SUMO
install), so **their runtime behaviour cannot be verified from this project**. Every reported
result came from `mini_backend.py`. Say exactly that if asked which simulator produced the
numbers.

### 2.3 Environment layer

| File | LOC | Status | Purpose | Imported by |
|---|---:|---|---|---|
| `envs/traffic_env.py` | 231 | CORE | the multi-agent env: `reset/step`, `_rewards()`, `_advance_one_second()` | 8 places incl. 3 test files |
| `envs/phases.py` | 141 | CORE | `SignalFSM` — the safety machine (min/max green, yellow, all-red) and `PhaseDef` set | `traffic_env`, `test_phases_safety` |
| `envs/spaces.py` | 82 | CORE | builds the 23-D observation, including neighbour features | `traffic_env`, `rl_controller`, `trainer`, `test_env` |

### 2.4 Agents

| File | LOC | Status | Purpose | Imported by |
|---|---:|---|---|---|
| `agents/double_dqn_agent.py` | 112 | CORE | the learner: Double-DQN target, PER sampling + priority update, target sync, Huber loss | `multi_agent`, `agents/__init__` |
| `agents/multi_agent.py` | 139 | CORE | one policy per intersection (shared weights by default), pooled buffer, portable save/load | `agents/__init__` |
| `agents/net.py` | 313 | CORE | **both** Q-nets: `TorchQNet` and a from-scratch `NumpyQNet` (manual backprop) behind `create_qnet` | `double_dqn_agent`, `test_replay` |
| `agents/replay.py` | 208 | CORE | sum-tree prioritized replay + a uniform buffer | `double_dqn_agent`, `qmix`, `test_replay` |
| `agents/policies.py` | 38 | CORE | linear ε-greedy schedule with `state_dict` | 4 modules |
| `agents/qmix.py` | 179 | **OPT** | QMIX monotonic mixing network for CTDE. Gated by `qmix.enabled: false`, and needs PyTorch. **Never executed in any shipped result** | `agents/__init__` (lazy) |
| `agents/dqn.py` | 86 | **OPT (reachable only via QMIX)** | a textbook `DuelingQNetwork` in idiomatic PyTorch. The executed net is `net.py`, not this | `agents/qmix.py` only |

**The trap here.** If a panellist asks "show me your DQN network", pointing at `agents/dqn.py`
is wrong — that file only runs if you switch QMIX on. The network that actually trained the
shipped checkpoint is `create_qnet` in `agents/net.py`, which on this machine returned
`NumpyQNet`. `agents/dqn.py` now says so in its own docstring.

### 2.5 Controllers

| File | LOC | Status | Purpose | Imported by |
|---|---:|---|---|---|
| `control/base.py` | 27 | CORE | the `Controller` interface every policy implements | the 3 controllers |
| `control/fixed_time.py` | 29 | CORE | baseline 1 — fixed 30 s split, 60 s cycle | `control/__init__` |
| `control/max_pressure.py` | 45 | CORE | baseline 2 — the throughput-optimal max-pressure rule | `control/__init__` |
| `control/rl_controller.py` | 64 | CORE | wraps `MultiAgentDQN` for greedy inference; loads the checkpoint | `dashboard/session`, `run.py`, `trainer` |
| `control/emergency.py` | 62 | CORE | rule-based preemption: forces the serving phase for an ambulance, still through the FSM | `control/__init__` |
| `control/__init__.py` | 25 | CORE | `build_controller(name, …)` factory | tests, `eval/metrics`, dashboard |

### 2.6 Training, evaluation, dashboard

| File | LOC | Status | Purpose | Imported by |
|---|---:|---|---|---|
| `train/trainer.py` | 183 | CORE | the episode loop, curriculum, periodic greedy eval, checkpointing, CSV logging | `run.py` |
| `train/curriculum.py` | 20 | CORE | `scenarios[episode % 4]` — cycles low→medium→high→rush | `train/__init__` |
| `eval/benchmark.py` | 139 | CORE | the 3×4×3 sweep, `improvement_table`, writes both CSVs + `benchmark_summary.md` | `run.py`, `plots`, `test_benchmark_math` |
| `eval/metrics.py` | 55 | CORE | `run_controlled_episode()` — one episode under one controller; injects the ambulance at 40 % of the episode | `eval/benchmark` |
| `eval/plots.py` | 127 | CORE | the four Matplotlib PNGs | `run.py` |
| `dashboard/session.py` | 215 | CORE | the live demo brain: runs **RL and fixed-time side by side on identical traffic**, keeps rolling KPI deques | `server`, `stdlib_server` |
| `dashboard/server.py` | 144 | CORE | FastAPI + WebSocket broadcast every `tick_ms`; falls back if FastAPI is missing | `run.py` |
| `dashboard/stdlib_server.py` | 112 | **CORE-alt** | `ThreadingHTTPServer` + a stepper thread + `/api/state` polling. **This is the path that ran here** | `dashboard/server.py` |
| `static/app.js` | 666 | CORE | canvas network animation, hand-drawn line charts (`drawLineChart`), all controls. **(1.1.0)** replaced by `static/js/{main, transport, model, renderer, sprites, charts, ui}.js` |  — |
| `static/index.html` | 129 | CORE | the single page | — |
| `static/styles.css` | 164 | CORE | styling | — |

### 2.7 Tests (26 cases, all passing)

| File | Cases | What it actually asserts |
|---|---:|---|
| `tests/test_phases_safety.py` | 5 | the headline safety claim — over thousands of random phase requests, no two conflicting approaches are ever green together; min/max green respected |
| `tests/test_replay.py` | 5 | sum-tree priorities, sampling weights, capacity eviction, and the Q-net forward/backward shapes |
| `tests/test_controllers.py` | 5 | each controller returns a legal action for every agent; max-pressure picks the higher-pressure phase |
| `tests/test_env.py` | 4 | observation is 23-D and finite; `step()` advances 5 s; agent set matches the topology |
| `tests/test_reward.py` | 3 | reward sign and the switch penalty; the emergency bonus term |
| `tests/test_benchmark_math.py` | 4 | `improvement_table` arithmetic and grid/neighbour topology |
| `tests/conftest.py` | — | session-scoped `cfg` fixture |

**Coverage gaps — know these before a panellist finds them.** Nothing tests: the Double-DQN
target computation itself, `MultiAgentDQN.save/load` round-tripping, `RLController`, the
dashboard/session, `netgen`, `sumo_backend`, `qmix.py`, `agents/dqn.py`, or the claim *"RL beats
fixed-time"*. The last one is covered by the benchmark instead of by a test — which is
defensible (it is a 36-run experiment, not a unit assertion), but say it that way.

### 2.8 Non-code files

| File | Size | Status | Note |
|---|---:|---|---|
| `config.yaml` | 7.4 KB | CORE | single source of truth; every module reads it |
| `models/pretrained/atsc_2x2.pt` | 321 KB | CORE | **the checkpoint behind every RL number.** Verified: 19,971 float64 parameters, tagged `ep70_wait27.0` |
| `models/pretrained/atsc_2x2_final.pt` | 321 KB | CORE (spare) | the end-of-training weights; `run.py` loads `atsc_2x2.pt` |
| `outputs/benchmark_results.csv` | 5.8 KB | GEN | 36 raw runs — reproduces bit-exactly |
| `outputs/benchmark_summary.{csv,md}` | 3.0 KB | GEN | the aggregated tables the docs quote |
| `outputs/logs/train.csv` | 6.1 KB | GEN | **112 episodes**, ε 0.99 → 0.05 |
| `outputs/logs/train_quick.csv` | 509 B | GEN | an 8-episode smoke run |
| `outputs/*.png` (4) | 192 KB | GEN | the report figures |
| `docs/screenshots/*.png` (5) | 296 KB | DOC | committed copies so the README renders on GitHub |
| `requirements.txt` / `environment.yml` | 2.8 KB | CORE | every version pinned with `==` |
| `run.bat` / `run.sh` | 4.0 KB | CORE | one-click launchers (venv + install + demo) |
| `asgi.py` | 2.4 KB | CORE | the hosted entrypoint: exposes a module-level `app` because `build_app()` is a factory |
| `render.yaml` / `Dockerfile` / `.dockerignore` / `Procfile` / `requirements-deploy.txt` | 6.6 KB | CORE | the deployment set; `requirements-deploy.txt` is torch-free, which is what fits a 512 MB free instance |
| `pytest.ini` | 205 B | CORE | test discovery config |
| `traffic_mgmt_RL_major_project.pptx` | 1.3 MB | DOC, not committed | the deck — a submission artefact, handed in separately |
| `ATSC_Traffic_Signal_RL_final.zip` | 1.16 MB | not committed | a zip of the published set; attached to the GitHub Release instead of living in git |
| `package-lock.json` | 86 B | DEAD, not committed | an empty npm lockfile; there is no Node project. It only invites the question "where is your JS build?" |
| `.venv/` | ~300 MB | local only | already in `.gitignore`; never commit it |

---

## 3. Execution flow — one decision, traced through the code

Follow this with a finger on the screen; it is the single most convincing thing you can do
in a code walkthrough.

```
run.py eval
  └─ eval/benchmark.run_benchmark(cfg, seeds=[0,1,2])
      └─ for controller × scenario × seed:  eval/metrics.run_controlled_episode()
          ├─ MultiAgentTrafficEnv(cfg)                    envs/traffic_env.py
          │   └─ make_backend(cfg)  → MiniBackend         sim/__init__.py
          ├─ build_controller("rl", …) → RLController     control/rl_controller.py
          │   └─ MultiAgentDQN.load("atsc_2x2.pt")        agents/multi_agent.py
          └─ while not done:                              ← 360 iterations (1800 s / 5 s)
              ├─ obs      = env._observations()            envs/spaces.py     (23 numbers × 4)
              ├─ actions  = controller.act(obs)            argmax Q(s,·)
              └─ env.step(actions)
                  ├─ fsms[i].request(a)                    envs/phases.py     ← safety gate
                  ├─ emergency_ctrl.apply(env)             control/emergency.py
                  ├─ ×5:  _advance_one_second()
                  │        ├─ fsm.tick()  → active_green() (empty during yellow/all-red)
                  │        ├─ backend.set_green(...)       sim/mini_backend.py
                  │        └─ backend.step()               spawn · transit · discharge · accrue wait
                  └─ _rewards(switched, cleared)
```

**The one sentence that matters:** the agent's output is a *request*, and `fsms[i].request()`
is the only door into the signal — so no policy, trained or random, can produce a conflicting
green. Safety is structural, not learned.

**Timing arithmetic to have ready:** 1 control step = 5 simulated seconds = 5 backend ticks.
An evaluation episode is 1800 s → **360 control steps**. A training episode is 1200 s →
**240 control steps**. 112 training episodes × 240 = **26,880 control steps**, which is exactly
the ε-schedule counter stored in `atsc_2x2_final.pt`; the shipped best model `atsc_2x2.pt`
stores **16,800 = 70 × 240**, matching its own `ep70_wait27.0` tag. That arithmetic is how you
prove both checkpoints came from the training run in `outputs/logs/train.csv`.

---

## 4. RL formulation, grounded in the code

### 4.1 State — 23 numbers per agent (`envs/spaces.py`)

| Block | Count | Normalisation |
|---|---:|---|
| queue length per approach (N,S,E,W) | 4 | `/ 20` |
| accumulated waiting time per approach | 4 | `/ 100` |
| current phase, one-hot | 2 | — |
| green time elapsed | 1 | fraction of `max_green_s` |
| emergency present per approach | 4 | 0/1 flag |
| **neighbour pressure** (one per side) | 4 | `tanh(pressure / 40)` |
| **neighbour current phase** | 4 | phase index / n_phases |

The last 8 numbers are the entire coordination mechanism. `tanh` is there so a neighbour in
gridlock saturates instead of dominating the input scale. Missing neighbours (grid boundary)
contribute 0.

### 4.2 Action — `Discrete(2)`

Which phase should be green next: NS or EW. `phase_scheme: ns_ew` in config; `quad` (4 phases)
is supported by `PhaseDef` but was not trained. The agent chooses every 5 s and may re-choose
the phase it is already showing (i.e. "hold" is an action).

### 4.3 Reward (`traffic_env._rewards`, weights in `config.yaml → reward`)

```
r_i  =  − w_wait · delay_i / N
        − w_pressure · max(0, pressure_i) / N
        − w_switch · switched_i
        + w_emergency · cleared_i

w_wait 1.0 · w_pressure 0.30 · w_switch 0.20 · w_emergency 5.0 · N (normalize) 40.0
```

`delay_i` is vehicle-seconds of halting accumulated at intersection *i* during the 5 s
interval; `pressure_i` is the max-pressure quantity (incoming minus outgoing queue), clipped at
zero; `switched_i` is 1 if a phase change started; `cleared_i` counts emergency vehicles that
crossed. Dividing by 40 keeps per-step rewards roughly in [−10, 0] so the Q-targets stay in a
range Adam handles at lr 5e-4.

**Why each term exists — the "why chain" a panellist will walk:** minimise waiting → but a
greedy wait-minimiser oscillates the lights every 5 s, which is unsafe-feeling and wastes
clearance time → so penalise switching → but a switch penalty alone lets a queue starve →
so add pressure, which grows with the *imbalance* and forces service → and preemption needs a
reason to exist inside the objective → so pay 5.0 for clearing an ambulance.

**One honest caveat.** The `w_emergency` term only ever fires on the built-in backend:
`SumoBackend.pop_emergency_cleared()` returns 0 because SUMO's arrival list is never scanned
for the emergency vtype. Preemption itself works on both backends because it acts on the phase
request, not on the reward. This is now stated in the `sumo_backend.py` docstring.

### 4.4 Learner (`agents/double_dqn_agent.py`, `agents/net.py`)

| Piece | Implementation | Why |
|---|---|---|
| **Double DQN** | online net picks `argmax`, target net supplies the value: `y = r + γ·Q_target(s', argmax_a Q_online(s',a))` | decouples selection from evaluation, removing the max-operator's optimism bias |
| **Dueling head** | `Q = V(s) + (A(s,a) − mean_a A(s,a))` | in signal control "hold" and "switch" are often near-equal; separating state value from action advantage learns faster |
| **PER** | sum-tree, α 0.6, β 0.4→1.0 over 20,000 steps, ε 1e-6 | rare congested transitions carry the learning signal; importance weights undo the sampling bias |
| **Target network** | hard copy every 500 learner steps | a moving target diverges |
| **Loss** | Huber (TD error clipped to ±1 in the NumPy path) | outlier-robust when a queue explodes |
| **Optimiser** | Adam, lr 5e-4, batch 64, γ 0.99, grad clip 10.0 (torch only) | — |
| **Exploration** | linear ε 1.0 → 0.05 over 22,000 control steps | the full run is 26,880 steps, so ε sat at the 0.05 floor for the last ~18 % (from episode 91). Note the *selected* model was saved at episode 70, when ε was still ≈0.27 — selection uses greedy episodes, so exploration noise does not enter the score |
| **Parameter sharing** | one net for all 4 intersections; all transitions pooled | the intersections are homogeneous, so this is 4× the data for one net — standard shared-parameter independent Q-learning. `share_parameters: false` gives four separate nets |

Verified network size: **19,971 parameters** (23→128→128→ dueling heads, float64).

### 4.5 Safety FSM (`envs/phases.py`)

`GREEN → YELLOW → ALL_RED → GREEN`, with `min_green 10 s`, `max_green 60 s`, `yellow 3 s`,
`all_red 2 s`. Two properties do the work: a request arriving before `min_green` is **ignored**
(no flicker), and `active_green()` returns the **empty set** during both YELLOW and ALL_RED, so
during clearance every approach is red. `max_green` ends any green after 60 s — but it is
**not** a full anti-starvation guarantee **(1.1.0, corrected)**: the FSM accepts a new
request during amber and all-red, so a policy that re-requests the running phase cancels the
change and the side street keeps waiting. With the shipped policy the longest wait for a
green reached ~113 s. Conflicting greens remain impossible.

---

## 5. Provenance: what was actually trained and measured

A chain of custody for the results. Every link was verified.

| Link | Evidence |
|---|---|
| Training ran **112 episodes**, not the 100 the config used to say | 112 rows in `outputs/logs/train.csv`, episodes 0–111 |
| Those episodes used the 4-scenario curriculum evenly | 28 episodes each of low / medium / high / rush |
| Learning happened | first episode of each scenario → last: low 58.9→22.6 s, medium 85.4→28.4 s, high 178.0→41.6 s, rush 148.1→41.9 s. Best logged episode: **21.73 s** (ep 80, low) |
| ε annealed fully | 0.99 → 0.05 |
| The shipped checkpoint is from that run | its ε counter reads exactly **16,800 = 70 × 240** control steps, and its internal tag is `ep70_wait27.0` — the two agree independently. `atsc_2x2_final.pt` reads **26,880 = 112 × 240**, the full run |
| The checkpoints also fingerprint the replay warm-up | both files show `learner_steps` exactly **249 below** their ε counter (16,551 and 26,631). 4 transitions per control step ÷ `min_buffer = 1000` → the first 249 steps could not train. That offset being identical in both files is a strong internal consistency check |
| The benchmark used **3 seeds (0, 1, 2)**, not 5 | `seeds: [0, 1, 2]` is stamped into `outputs/benchmark_summary.md`; `config.yaml` now says the same |
| The benchmark is reproducible | all **36/36** rows regenerate bit-exactly from source + checkpoint |
| All controllers saw identical traffic | the arrival RNG is seeded per (scenario, seed) inside the backend, before any controller acts |

**How to say it:** "Three seeds, four densities, three controllers — 36 runs, and the CSV
regenerates exactly, so the table is auditable rather than anecdotal. Five seeds would tighten
the error bars; three is what we ran and three is what we report."

---

## 6. Results — every metric, recomputed from the raw CSV

Means over seeds 0–2, 1800 s episodes, 30 s warm-up excluded, built-in backend.

### 6.1 The full picture

| Metric | Scenario | Fixed-time | Max-pressure | **RL** | **RL vs fixed** |
|---|---|---:|---:|---:|---:|
| **Avg wait (s)** | low | 26.29 | 19.22 | 22.31 | **−15.1 %** |
| | medium | 34.33 | 22.52 | 25.24 | **−26.5 %** |
| | high | 68.66 | 35.56 | 35.17 | **−48.8 %** |
| | rush | 113.00 | 50.54 | 43.89 | **−61.2 %** |
| **Avg queue (veh/int.)** | low | 2.97 | 2.16 | 2.51 | −15.3 % |
| | medium | 6.79 | 4.44 | 4.98 | −26.6 % |
| | high | 20.07 | 10.37 | 10.25 | −48.9 % |
| | rush | 36.54 | 16.31 | 14.14 | −61.3 % |
| **Throughput (veh)** | low | 808.0 | 808.7 | 807.0 | −0.1 % |
| | medium | 1399.0 | 1400.3 | 1399.7 | +0.0 % |
| | high | 2002.0 | 2051.0 | 2057.7 | **+2.8 %** |
| | rush | 2093.3 | 2246.0 | 2253.3 | **+7.6 %** |
| **Moving-fraction speed (m/s)** | low | 5.25 | 6.39 | 5.79 | +10.1 % |
| | medium | 4.44 | 5.79 | 5.38 | +21.0 % |
| | high | 2.62 | 4.27 | 4.31 | +64.8 % |
| | rush | 1.76 | 3.34 | 3.67 | **+108.1 %** |
| **Fuel proxy (ml)** | low | 17,982 | 16,254 | 16,993 | −5.5 % |
| | medium | 34,682 | 29,590 | 30,757 | −11.3 % |
| | high | 72,164 | 51,676 | 51,424 | −28.7 % |
| | rush | 109,424 | 67,559 | 62,898 | **−42.5 %** |
| **CO₂ proxy (g)** | low → rush | 41,539 → 252,769 | 37,547 → 156,060 | 39,253 → 145,295 | −5.5 % → **−42.5 %** |
| **Emergency clearance (s)** | low | 26.0 | 26.3 | 20.0 | −23.1 % (1.30×) |
| | medium | 28.7 | 36.7 | 18.3 | −36.0 % (1.56×) |
| | high | 80.0 | 42.0 | 41.7 | −47.9 % (1.92×) |
| | rush | 113.7 | 39.3 | 32.7 | **−71.3 % (3.48×)** |

### 6.2 Three things in that table that need a prepared answer

**(a) Throughput barely moves (−0.1 %, 0.0 %, +2.8 %, +7.6 %) while the deck promised
+35–45 %.** This is the most likely gotcha in the whole viva, and the honest answer is strong:

> Below saturation, throughput is set by **demand**, not by the signal. Every vehicle that
> enters clears under both controllers within the episode, so the counts must match — what
> differs is how long each vehicle waited. Throughput can only improve when capacity actually
> binds, and that is exactly where we see it: +2.8 % at high and **+7.6 % at rush**. The
> +35–45 % on the slide is a pre-implementation *expected* figure; the measured result taught
> us that waiting time, not throughput, is the right headline metric below saturation.

**(b) "Average speed" is not a speedometer reading.** `mini_backend` computes
`mean_speed = free_speed × moving / present` — the free-flow speed scaled by the *fraction of
vehicles in motion*. Call it "the share of vehicles moving rather than queued". Never call the
rush figure "a 108 % speed increase" — say "roughly twice as many vehicles in motion at any
instant". A microscopic per-vehicle speed distribution **cannot be obtained from this
project's built-in backend**; SUMO would give it.

**(c) Max-pressure beats RL at low and medium density.** 19.22 vs 22.31 s (low) and
22.52 vs 25.24 s (medium). Do not hide it — it is evidence of an honest baseline:

> Max-pressure is provably throughput-optimal and near-optimal for light demand, where the
> right move is simply "serve whoever is waiting". The learned policy has to discover that,
> and it pays a small switch-penalty tax for doing so. Where the problem is genuinely hard —
> high and rush, with coordination and spillover — RL matches it (35.17 vs 35.56 s) and then
> beats it clearly (43.89 vs 50.54 s, a 13 % further reduction). Beating fixed-time was the
> objective; getting within noise of max-pressure at low load and past it under congestion is
> the result.

### 6.3 The stability result the docs do not yet make enough of

Seed-to-seed spread in average wait (max − min across seeds 0–2):

| Scenario | Fixed-time | Max-pressure | **RL** |
|---|---:|---:|---:|
| low | 0.94 s | 1.44 s | **0.57 s** |
| medium | 1.08 s | 2.75 s | 2.17 s |
| high | 14.53 s | 3.73 s | **2.35 s** |
| rush | **30.02 s** | 15.18 s | **2.69 s** |

At rush the RL controller is **~11× more consistent** than fixed-time (2.69 s vs 30.02 s) and
~5.6× more consistent than max-pressure. That is arguably a better selling point than the mean
itself: a fixed-time junction under heavy load is a lottery depending on how the arrivals
happen to land, while the adaptive controller lands in the same place every time. Predictability
is what a traffic authority actually buys.

### 6.4 Graph by graph

| Figure | What it plots | What to say | What to *not* claim |
|---|---|---|---|
| `comparison_kpis.png` | grouped bars: wait, queue, throughput × 4 densities × 3 controllers | "the wait and queue bars separate more as load rises; the throughput bars are almost equal by design, because demand is the binding constraint below saturation" | that throughput improved much |
| `rl_improvement.png` | RL % improvement vs fixed-time, with a shaded 40–50 % band | "the band is the **proposal's target**, hardcoded in `plots.py:80` — not a measured value. We cross it at high (48.8 %) and clear it at rush (61.2 %)" | that the band is a result |
| `emergency_clearance.png` | clearance time by controller × density | "1.30× at low up to **3.48× at rush** — preemption matters most when there is something to preempt" | "10× faster" — it is not |
| `training_curve.png` | avg wait vs episode | "the curve is **saw-toothed because the curriculum cycles low→medium→high→rush every four episodes** — the four interleaved bands each trend down. Compare like scenarios, not adjacent points" | that a spike is instability |

The training curve's shape is a question waiting to be asked. The per-scenario trend is the
answer: low 58.9→22.6, medium 85.4→28.4, high 178.0→41.6, rush 148.1→41.9 s.

---

## 7. Documentation-vs-code contradictions — found, and now fixed

Every item below was a real disagreement between what a document claimed and what the code
does. All are now corrected in the repo. Keep this table: "we audited our own docs and fixed
fourteen inconsistencies" is a better answer than being caught by one.

| # | Where | The claim | The reality | Fix applied |
|---|---|---|---|---|
| 1 | `REPORT.md` §7 | "beating fixed-time by **~41 %**" | 37.9 % mean over the four densities | reworded to ~38 % (61 % at rush) |
| 2 | `REPORT.md` §10 | "achieves the 40–50 % target (mean ≈41 %, **up to 64 %**)" | mean ≈38 %, best 61.2 % | reworded: reaches the band in the congested regimes (49 %, 61 %) with a 38 % overall mean |
| 3 | `REPORT.md` §5 | "3–5 identical seeds" | exactly 3 (0, 1, 2) | stated precisely |
| 4 | `EXPLAINER.md` §4 | frontend uses "**Chart.js** (loaded from a CDN)" | there is no Chart.js and no CDN request anywhere; charts are hand-drawn in `drawLineChart()` | corrected — and it now supports the offline claim instead of contradicting it |
| 5 | `README.md` viva table | RL algorithm = `agents/double_dqn_agent.py`, **`agents/dqn.py`** | `dqn.py` is only reachable via disabled QMIX; the live net is `agents/net.py` | repointed to `net.py` |
| 6 | `agents/dqn.py` docstring | implied it was *the* network | it is a reference implementation, imported only by `qmix.py` | docstring now says so explicitly |
| 7 | `sim/netgen.py` docstring | writes `grid.con.xml` + `grid.tll.xml` with phase-aligned programs | neither file is ever written; TLS comes from `netconvert --tls.guess` and is overridden every step | docstring rewritten to describe the real design and why it is fine |
| 8 | `sim/sumo_backend.py` docstring | "renders those as `y`/`r`" | `_apply_states` only ever writes `G`/`r`; the FSM sends an empty green set through the whole clearance, so it shows all-red | docstring corrected; dead `self._yellowing` field removed |
| 9 | `sim/sumo_backend.py` | (silent) | `pop_emergency_cleared()` always returns 0, so `w_emergency` never fires on SUMO | documented in the module docstring |
| 10 | `config.py` docstring | rush `arrival_scale` is `1.2` | `config.yaml` says `1.00` | docstring corrected |
| 11 | `mini_backend.py`, `curriculum.py`, `config.yaml` comments | rush demand is time-varying | every scenario sets `time_varying: false`; `_time_factor()` is inactive | all three corrected — rush is *sustained* heavy demand |
| 12 | `config.yaml` | `eval.seeds: [0,1,2,3,4]`, `train.episodes: 100` | the shipped artefacts used 3 seeds and 112 episodes | config now matches the artefacts, so a re-run reproduces them |
| 13 | `README.md` results | "running on the **SUMO** microscopic simulator" as the headline claim, directly above the results table | every reported number was measured on the built-in point-queue backend; SUMO is implemented but unmeasured | headline now says "either SUMO or the built-in point-queue simulator", and the results section states the provenance and links `outputs/benchmark_results.csv` |
| 14 | `README.md` | "*Screenshots are placeholders until you run `eval`*" | `docs/screenshots/*.png` are byte-identical (`md5sum`) to the real `outputs/*.png` from the 36-run benchmark | caption corrected — they are the real plots, copied so they render on GitHub |

Two small pieces of dead code were also removed: a no-op `if fsm.switch_started: pass` loop in
`traffic_env.step()` and an unused local `import Config` in `netgen.py`.

**A reproducibility bug was found and fixed in the process.** The dashboard's
vehicle-type feature was drawing from the *main* RNG (`_pick_kind(self._rng)`), which consumed
one random number per spawned vehicle and shifted the entire arrival stream — so the shipped
CSV could no longer be regenerated (26.038 s instead of 26.879 s on the first cell). Cosmetic
draws now come from a separate stream, `make_rng(seed, "kind")`, and all 36 rows match again.
This is a genuinely good story to tell: *"we caught a seeding bug by trying to reproduce our own
results table, and the fix was to give cosmetic randomness its own stream."*

A packaging fix went in alongside it: the shipped checkpoints were pickled by NumPy 2, whose
arrays reference `numpy._core`, but `requirements.txt` pins `numpy==1.26.4` for torch/SUMO ABI
safety — so a fresh install would have failed to load the model. `MultiAgentDQN.load` now uses a
`_CompatUnpickler` that falls back to `numpy.core`. Verified against a simulated NumPy-1 module
map: plain `pickle.load` raises `ModuleNotFoundError`, the compat loader returns byte-identical
weights. **Caveat: this was tested with a simulated module map, not a real `numpy==1.26.4`
install**, so the real-install path cannot be verified from this environment.

---

## 8. Weaknesses — stated before a panellist finds them

Ordered by how likely they are to be attacked. Each has the concession *and* the recovery.

1. **No real traffic data.** Demand is Poisson with an arterial boost, not a survey of a real
   junction. → *"The controller is validated against two baselines under identical synthetic
   demand, which isolates the control policy. Calibrating arrival rates to real counts is the
   first thing I would do with field data, and nothing in the design depends on the arrival
   process being synthetic."*
2. **The reported numbers come from the fallback simulator, not SUMO.** SUMO is the configured
   primary and the code is complete, but it is not installed here, so the built-in point-queue
   model produced every result. → *"Point-queue means no lane-changing and no spill-back, so it
   is optimistic about capacity. It is also why the numbers are exactly reproducible. SUMO is a
   config flip, and the same checkpoint loads."*
3. **2×2 grid, 4 agents.** Small. → *"Deliberate: it demonstrates multi-agent coordination —
   which one junction cannot — while keeping the demo reliable. `grid_rows: 3` works; it needs a
   retrain, and shared parameters mean the network doesn't grow."*
4. **No time-of-day feature in the state.** The agent cannot see a surge coming, only the queue
   it already has. This is why all four scenarios are stationary. → *"Adding a short-horizon
   arrival-rate estimate, or a GRU over recent observations, is the principled fix."*
5. **Max-pressure wins at low/medium.** See §6.2(c).
6. **Throughput gain is small.** See §6.2(a).
7. **Three seeds, no confidence intervals.** → *"Three seeds, and I report the spread rather
   than hiding it: at rush RL varies by 2.69 s against fixed-time's 30.02 s. Five seeds would let
   me put error bars on the chart."*
8. **112 episodes is a short training run.** ~26,880 control steps. → *"It converged for this
   problem size — the last low-density episodes sit at 22.6 s against 58.9 s for the first, and
   the best greedy-eval score arrived at episode 70 with the remaining 42 episodes failing to
   beat it, which is direct evidence of a plateau rather than a truncation. A full run takes
   33 seconds, so length was never the binding constraint — problem size was."*
9. **Emergency preemption is rule-based, not learned.** → *"On purpose. Emergency response is a
   safety function; you do not want it to be a learned probability. The RL policy handles the
   surrounding traffic, the rule handles the ambulance, and the reward pays for clearing it so
   the policy learns to cooperate."*
10. **`w_emergency` never fires on the SUMO backend** (§7 item 9), so an emergency-aware policy
    trained on SUMO would lose that signal.
11. **The dashboard has no authentication.** Locally it binds `127.0.0.1`, so the exposure is
    local. The hosted copy (see `docs/DEPLOYMENT.md`) is public and still unauthenticated —
    that is a deliberate choice for a demo, not an oversight: there is no account, no upload,
    no database and no filesystem write behind `POST /api/cmd`, so the worst a stranger can do
    is pause the simulation or change the scenario. What a real deployment would need, and
    this does not have: auth on `/api/cmd`, a rate limit, an origin check, and one session per
    visitor instead of one shared session. Say exactly this if asked about deployment — it is
    a bounded gap that was measured, not a hypothetical one.
12. **Coverage gaps in the test suite** (§2.7).
13. **QMIX is written but never run** (§9).
14. **The metrics are proxies.** Fuel is `0.30 ml/s halted + 0.90 ml/s moving`; CO₂ is fuel ×
    2.31 g/ml. Defensible as a *relative* comparison between controllers on identical traffic;
    not an emissions measurement. Never quote the absolute litres.

---

## 9. Improvements, ranked by effort — and the honest verdict on each

### Level 1 — hours, no retrain, zero risk to the demo

| # | Change | Payoff |
|---|---|---|
| L1.1 | Re-run `eval` with 5 seeds and put min/max error bars on `comparison_kpis.png` | kills the "only three seeds" objection outright |
| L1.2 | ~~Delete `package-lock.json` and the in-repo zip~~ — **done**: both are in `.gitignore`, so the published set is 95 files with no npm artefact and no zip-inside-the-zip | a clean public repo; no "where's your JS build?" question |
| L1.3 | Add a *measured* line to `rl_improvement.png` alongside the aspirational band, or label the band "proposal target" in the legend | removes the only figure that can be read as overclaiming |
| L1.4 | Rename the `avg_speed` column to `moving_fraction_speed` in the CSV writer | the metric stops being able to mislead you under pressure |
| L1.5 | Add the §6.2 answers to `EXPLAINER.md` §14 as three more Q&A entries | the throughput question becomes a prepared answer |

### Level 2 — a day, small code change, needs a re-run but not a redesign

| # | Change | Payoff |
|---|---|---|
| L2.1 | Unit-test the Double-DQN target and `save/load` round-trip | closes the two most conspicuous coverage gaps (§2.7) |
| L2.2 | Increment `_emerg_cleared` in `SumoBackend` from the arrival list | makes the emergency reward backend-independent |
| L2.3 | Fractional dashboard stepping (accumulator + slider `min="0.25"`) so the ambulance is visible in real time | fixes the one live-demo annoyance — but it touches a frozen, verified build, so do it only with time to re-verify. **Zero-code workaround: Pause → Emergency → Step, Step, Step** |
| L2.4 | Log per-episode greedy eval on a *fixed* scenario as well as the curriculum one | a monotone training curve to show instead of the saw-tooth |

### Level 3 — a week, real research value

| # | Change | Payoff |
|---|---|---|
| L3.1 | Add a demand-trend feature (EWMA of arrivals per approach) to the observation and retrain | directly removes weakness #4; makes a genuinely time-varying rush scenario winnable |
| L3.2 | Turn on a real time-varying rush (`time_varying: true`) and report it as a fifth scenario | tests adaptivity in the regime that most needs it |
| L3.3 | Run the whole benchmark on SUMO and publish both tables side by side | converts weakness #2 into a validation study |
| L3.4 | 3×3 grid retrain | shows the shared-parameter design scales without growing the network |

### Level 4 — beyond a semester

Multi-lane phases with protected turns; real OSM network import; a learned emergency policy;
transfer from simulation to a hardware-in-the-loop controller; distributed training.

### Priority verdict

| Priority | Do this | Because |
|---|---|---|
| **1** | L1.1 (5 seeds) | highest credibility gain per hour |
| **2** | L1.3 + L1.4 (label the target band, rename the speed column) | removes every remaining way the figures can be misread |
| **3** | L1.5 (write the throughput answer into the docs) | the likeliest question, currently unanswered on paper |
| **4** | L2.1 (test the DQN target) | the gap a sharp examiner will probe |
| **5** | L3.1 (demand-trend feature) | the one change that would move the numbers, not just the presentation |

### Advanced features: keep, or drop?

| Feature | Status in repo | Verdict |
|---|---|---|
| **QMIX / CTDE** | `agents/qmix.py`, 179 lines, `qmix.enabled: false`, needs torch, **never executed** | **Keep, and describe it accurately as future work.** Do not imply it produced results. If asked "why is it off?": *"QMIX needs a centralised critic and PyTorch; our shipped result had to run without PyTorch, and with 4 homogeneous agents and neighbour observations the shared-parameter design was already sufficient. It is scaffolded for the next stage."* Presenting it as a delivered feature is the single biggest honesty risk in the project |
| **Camera / YOLO vehicle counting** | mentioned as future work only; **no code exists** | Never imply otherwise. *"Out of scope; the observation interface takes queue counts, so a vision front-end would substitute for the detector without touching the agent."* |
| **Transformer / GNN policy** | not present | honest future work; a GNN is the natural fit for neighbour features |
| **Real-map (OSM) import** | not present; `netgen` builds synthetic grids | future work, and a realistic next step because SUMO ingests OSM directly |
| **PyTorch path** | implemented, not exercised here | a graded dependency-optional feature — keep, and be clear the shipped run used the NumPy net |

---

## 10. Real-world deployment — what would actually be needed

The gap between this project and a live junction, in the order the gap has to be closed.

**Sensing.** The observation needs per-approach queue length and accumulated wait. Real sources:
inductive loops (accurate, expensive to install), magnetometers, radar, or camera + detector.
All of them give noisy, occasionally-missing readings, so the state builder would need
imputation and a stale-data fallback. Nothing in this project handles sensor failure.

**Safety and certification.** The FSM already encodes the right structure (min/max green,
amber, all-red), but a real controller must satisfy the local signal standard, provide a
fail-safe (drop to flashing amber or a fixed plan on any fault), and be auditable. In practice
the RL policy would run as an *advisory* layer proposing plans to a certified controller that
can veto — which is exactly the architecture already used here, with the FSM as the certifier.

**The sim-to-real gap.** A policy trained on a point-queue model would not transfer. The route
is: calibrate SUMO to real counts → train in SUMO with domain randomisation over arrival rates
and saturation flow → shadow-mode deployment (log what the policy *would* have done, compare
against the existing plan) → limited live trial at one junction with a supervisor.

**Operations.** Model versioning and rollback, latency budget (a 5 s decision interval is
generous — inference here is a 23→128→128→2 forward pass, microseconds), monitoring for
distribution shift, and a way to explain any decision to a traffic engineer after the fact.

### Can Vercel host this? — No, and here is the precise reason

The honest technical answer, which is also a good viva answer about architecture:

**The dashboard is a stateful, continuously-running simulation.** `dashboard/session.py` holds
two live environments (RL and fixed-time) in memory, and a background thread advances them every
`tick_ms: 200` whether or not anyone is looking. The browser then polls `/api/state` — or holds a
WebSocket — to watch that shared state evolve.

Vercel Functions are **request-scoped and stateless**: each invocation gets a fresh sandbox,
nothing is guaranteed to persist between requests, and a process is not kept alive between
invocations to run a stepper thread. Long-running WebSocket servers are not part of the
Functions model either. Vercel *has* raised duration limits substantially — Fluid compute
functions default to a 300 s maximum and can extend to 800 s, with up to 30 minutes on
Pro/Enterprise — but a longer single invocation is not the same thing as a persistent process
plus shared memory, which is what this design needs. There is also a hard packaging limit
(250 MB unzipped for Python functions) that PyTorch alone would breach, though this project's
NumPy fallback would fit comfortably.

**What Vercel is genuinely good for here:** the static front-end. `index.html`, `app.js` and
`styles.css` are three plain files with zero dependencies, so they deploy to Vercel's CDN
instantly.

**Three workable options, in order of effort:**

| Option | How | Trade-off |
|---|---|---|
| **Keep it local (recommended for the viva itself)** | `python run.py demo` → `127.0.0.1:8000` | zero risk, no network needed, no cold start, nothing to go wrong mid-demo. This is what the project was designed for |
| **A persistent container host — this is what ships** | Render free plan, defined in `render.yaml`, entrypoint `asgi.py`, started as `uvicorn asgi:app --host 0.0.0.0 --port $PORT`. Full walkthrough in `docs/DEPLOYMENT.md` | the design fits unchanged, and the free instance sleeps after 15 min idle (~50 s to wake). `POST /api/cmd` stays unauthenticated on purpose (§8 item 11) — a visitor can pause your demo, and nothing worse |
| **Split it** | static front-end on Vercel + the simulation on a container host, with the front-end pointing at that API | more moving parts; needs CORS. Only worth it if you specifically want a vercel.app URL |

A fourth option worth knowing about because it is a clean fit for serverless: **make the
simulation stateless** by moving the step loop into the browser and using functions only for
inference. That would mean porting the point-queue model to JavaScript — a real project in its
own right, not a deployment tweak.

---

## 11. Question bank — 144 project-specific questions

Each row gives what to **say** (10–20 seconds, out loud) and what to add **if pushed** (the
technical depth plus the follow-up they will ask). ★ marks the ones most likely to be asked;
★★ marks the ones that decide the viva. Every number in every answer is traceable to
`config.yaml`, `outputs/*.csv` or a named source file.

### A. Motivation and framing (11)

| # | Question | Say this | If pushed |
|---|---|---|---|
| 1 ★ | Why reinforcement learning for traffic signals? | Signal control is a sequential decision problem where each choice changes the state you face next — that is exactly what RL is for. A timer ignores the queue; a rule reacts to it; RL learns a policy that anticipates consequences. | Formally it is a Markov decision process: the queue state evolves from your green choices, and the objective (cumulative delay) is a sum over time, not a single-step cost. Greedy rules optimise the current step; the discounted return optimises the trajectory. → *"So is your policy optimal?"* No — DQN gives no optimality guarantee; it beat both baselines empirically, which is the claim I make. |
| 2 | Why not just optimise the fixed timings offline? | That is Webster's method, and it is genuinely good for *stationary, known* demand. It cannot respond to the asymmetry and randomness of a real hour. | Webster minimises expected delay for a fixed cycle given average flows; the moment flows differ from the average, its allocation is wrong. Our fixed-time baseline is essentially that: 30 s each way. RL cuts its wait 15–61 % on the same traffic. |
| 3 | Why a grid and not one junction? | Because coordination is the interesting part. One junction cannot produce a green wave; four can. | The neighbour features in the observation (pressures and phases of adjacent junctions) only mean something with neighbours. With `arterial_boost: 2.2` the E–W corridor carries 2.2× the side-street demand, so there is a real corridor to coordinate. |
| 4 | Why is 2×2 enough? | It is the smallest network that has real coordination structure, and it keeps the demo reproducible in seconds. | Each junction has at least one neighbour on the arterial and one on a cross street, so every term in the observation is exercised. `grid_rows: 3` works and needs only a retrain; shared parameters mean the network size does not change. |
| 5 | What is your actual contribution? | Integration and rigour, not a new algorithm: multi-agent neighbour-aware coordination, a safety layer that makes unsafe states impossible, preemption, two honest baselines across four densities on identical traffic, and a build that runs with no external dependencies. | Every learning component is published work (DQN 2015, Double 2016, Dueling 2016, PER 2016). What is ours is the system: the FSM/agent separation, the observation design, the dependency-optional architecture, and a results table that regenerates bit-exactly. |
| 6 | Who benefits, in what units? | Drivers: 15–61 % less waiting. Emergency services: 1.3–3.5× faster corridor clearance. Environment: 5.5–42.5 % less fuel by our proxy. | All relative to fixed-time on identical traffic. The fuel and CO₂ figures are proxies computed from halted-vs-moving counts, so they are comparative, not measured emissions. |
| 7 | Why should a city trust a neural network with a traffic light? | It never gets to control the light. It requests a phase; a finite-state machine decides whether that request is legal. | Deployment would run the policy in shadow mode first (log what it would have done, compare with the live plan), then advisory mode behind the certified controller. The architecture already separates optimisation from safety. |
| 8 | What is the baseline you are trying to beat, honestly? | Both of them. Fixed-time is the deployed reality; max-pressure is the strong academic reference. | Choosing only fixed-time would have been the easy path. Max-pressure is provably throughput-optimal, and it beats us at low density — reporting that is the point. |
| 9 | Is this deployable today? | No. It is a validated simulation study with a clear deployment path. | The missing pieces are real sensing with failure handling, calibration to real counts, certification, and a shadow-mode trial (§10). |
| 10 | Why did you not use a published RL traffic library (RESCO, SUMO-RL)? | Building the loop myself is why I can answer questions about every line of it. | Those libraries wrap SUMO and hand you a Gym env; the learning, safety and evaluation code would still have been ours. Writing the env made the FSM/agent boundary explicit, which is the design point of the project. |
| 11 | What would you do differently starting over? | Put a demand-trend feature in the state from day one, and run five seeds from the start. | Those are exactly the two things a panellist can attack: no anticipation, and thin statistics. Both are cheap at the start and awkward to retrofit. |

### B. RL fundamentals they will test (15)

| # | Question | Say this | If pushed |
|---|---|---|---|
| 12 ★ | Define your MDP. | State: 23 numbers per intersection. Action: which phase gets green next, 2 choices. Reward: negative delay and pressure, minus a switch penalty, plus an emergency bonus. Transition: the simulator. γ = 0.99. | It is strictly a decentralised partially-observable MDP — each agent sees its own junction plus neighbour summaries, not the global state — which is why we use independent learners with shared weights rather than a single joint-action learner. |
| 13 | Why is it partially observable? | Each agent sees its own queues and a summary of its neighbours, never the whole network. | The joint state would be 4×23 plus everything in transit. Each agent's observation is a projection of it, so the process is a Dec-POMDP. Neighbour features are the deliberate minimum needed for coordination. |
| 14 ★ | What is γ = 0.99 doing? | Weighting the future. At a 5 s decision interval, 0.99 gives an effective horizon of roughly 100 steps — about 8 minutes of traffic. | 1/(1−γ) = 100 steps × 5 s = 500 s. Long enough that holding a green to let a platoon through pays off; short enough that credit assignment stays learnable. γ = 0.9 would be ~10 steps, too myopic for a green wave. |
| 15 | Model-free or model-based? | Model-free. The agent never predicts the next queue state; it only learns action values. | Model-based would mean learning the arrival and discharge dynamics and planning through them. With a simulator available, model-free was simpler and sufficient; model-based would matter if real interactions were scarce. |
| 16 | On-policy or off-policy? | Off-policy. That is what lets us learn from a replay buffer of old experience. | The behaviour policy is ε-greedy; the target uses the greedy action. An on-policy method like PPO could not reuse a 50,000-transition buffer, and prioritised replay would be meaningless. |
| 17 | Value-based or policy-gradient? Why? | Value-based. Two discrete actions, and we want the values themselves. | With `Discrete(2)` a policy gradient buys nothing — there is no continuous action to parameterise — and DQN's replay makes it far more sample-efficient. Having Q-values is also a demo feature: the dashboard can show what the agent thinks each phase is worth. |
| 18 | Write the Bellman optimality equation. | Q*(s,a) = E[r + γ·max_a' Q*(s',a')]. | Our loss is the Huber distance between Q(s,a) and the Double-DQN target y = r + γ·Q_target(s', argmax_a' Q_online(s',a')). The difference — argmax from online, value from target — *is* Double DQN. |
| 19 | What is the exploration–exploitation trade-off here, concretely? | Early on the agent must try holding a green too long to learn that it is bad. ε goes 1.0 → 0.05 over 22,000 control steps. | We trained 26,880 steps, so ε sat at the 0.05 floor for the last ~18 %, from episode 91 — that final stretch is effectively fine-tuning the greedy policy. Evaluation is fully greedy, ε = 0. |
| 20 | Why keep ε at 0.05 rather than 0? | A small floor keeps the buffer from collapsing onto one trajectory and keeps the target network honest. | It is the DQN-paper convention. At evaluation we set ε = 0 — `controller.act(obs, explore=False)` — so the reported numbers contain no random actions. |
| 21 | What is the credit-assignment problem in your setting? | A green held now causes delay three intersections away a minute later. The reward at *this* step cannot see that. | γ-discounted bootstrapping propagates it backwards through the value function over many updates; the neighbour features give the agent a local proxy for the downstream effect so it does not have to learn it purely from delayed reward. |
| 22 | Is your reward sparse or dense? | Dense — every 5 s step returns a delay-based reward. Only the emergency bonus is sparse. | Dense shaping is why 112 episodes suffice. If the only signal were "total delay at the end of the episode", 240 steps of credit assignment would need far more data. |
| 23 | What is the difference between the reward you optimise and the metric you report? | The reward is per-step delay plus penalties, normalised by 40. The metric is average waiting time per vehicle over the episode. | They correlate but are not identical: the reward includes the switch penalty and the pressure term, which are means to an end. Reporting the raw metric prevents me from optimising my own scoreboard. |
| 24 | What is a policy, in your code? | `argmax_a Q(s,a)` — one line in `RLController.act`. | The policy is not stored separately; it is implied by the Q-network. That is the defining property of value-based RL. |
| 25 | Could a bandit algorithm solve this? | No. A bandit assumes actions do not change future state; here the whole difficulty is that they do. | Holding NS green builds the EW queue you must serve next. That is state transition, so you need the full MDP machinery. |
| 26 | What would a random policy score? | Far worse than fixed-time, and the FSM would still keep it safe — which is the point worth making. | We do not report a random baseline because it is not a meaningful comparison; the safety test, however, effectively runs thousands of random requests and shows no unsafe state ever occurs. |

### C. State, action and reward design (16)

| # | Question | Say this | If pushed |
|---|---|---|---|
| 27 ★ | Why 23 dimensions — enumerate them. | 4 queues + 4 waiting times + 2 phase one-hot + 1 green-elapsed + 4 emergency flags + 4 neighbour pressures + 4 neighbour phases = 23. | Built in `envs/spaces.py`. The count changes with the phase scheme: `quad` (4 phases) would make it 4+4+4+1+4+4+4 = 25. The code derives it, so nothing is hardcoded. |
| 28 ★ | Why normalise, and why those constants? | To keep every input in roughly [0,1] so no feature dominates the first layer. Queues ÷ 20, waits ÷ 100, neighbour pressure through tanh. | Un-normalised, a 200-second wait would swamp a 0/1 phase flag and the first layer would effectively see one feature. tanh on neighbour pressure means a gridlocked neighbour *saturates* instead of exploding the input scale — a hard clip would lose the gradient, tanh keeps it. |
| 29 | Why include waiting time when you already have queue length? | They say different things. Ten cars that just arrived are not the same as three cars that have waited two minutes. | Queue is a spatial measure, accumulated wait is temporal. Fairness needs the second: without it a policy can keep a short queue permanently starved. |
| 30 | Why green-time-elapsed in the state? | Because the FSM's decision depends on it, so the agent must see it to predict what its request will do. | Expressed as a fraction of `max_green_s`. Without it the state would be non-Markov from the agent's point of view: identical queues could give different outcomes depending on hidden FSM timing. |
| 31 ★ | What exactly are the neighbour features, and why do they enable green waves? | Each neighbour contributes `tanh(pressure/40)` and its phase index. Seeing that the upstream junction just went green on the arterial tells the agent a platoon is coming. | This is the entire coordination mechanism — no central controller, no communication protocol, just observation. It is the "coordination through shared observation" pattern from cooperative MARL. → *"Prove there is a green wave."* I can show the dashboard's green-wave heuristic and the correlated phase alignment along the arterial; a strict proof would need platoon-level trajectory analysis, which **cannot be produced from the built-in backend**. |
| 32 | What happens to neighbour features at the grid boundary? | They are zero. A missing neighbour contributes nothing. | Consistent 23-D observations across all four agents is what makes parameter sharing possible; masking with zeros is the standard way to keep the shape fixed. |
| 33 | Why not give each agent the whole network state? | It does not scale and it is not realistic. A controller at one junction should not need a global view. | Global state is 4×23 plus in-transit vehicles, and it would grow quadratically with grid size. Local observation plus neighbour summaries is the design that survives scaling — and it is what a real distributed controller could actually sense. |
| 34 ★ | Why only 2 actions? | Two phases: North–South green or East–West green. It is the smallest action set that makes the problem real. | `PhaseDef` supports a 4-phase `quad` scheme with protected turns; we trained `ns_ew` because it matches the single-lane-per-approach network. More phases means a larger action space and more data — a scope decision, not a limitation of the code. |
| 35 | Is "hold the current phase" an action? | Yes — requesting the phase you already have. Doing nothing is a decision. | So the agent decides every 5 s whether to extend or switch, which is exactly the extend/terminate decision a real adaptive controller makes. |
| 36 | Why decide every 5 s and not every second? | 5 s is one saturated-flow discharge burst; per-second decisions would be 5× the data for choices the FSM would mostly ignore anyway. | `min_green_s: 10` means a switch request cannot take effect more often than every 10 s regardless. A 5 s interval keeps the agent responsive without wasting samples. Episode length 1800 s ÷ 5 s = 360 control steps. |
| 37 ★ | Walk me through the reward term by term. | Minus delay, minus positive pressure, minus 0.2 if you switched, plus 5 if you cleared an ambulance — the first two divided by 40. | Delay is vehicle-seconds halted in the interval; pressure is incoming minus outgoing queue clipped at zero; the switch penalty prices the lost clearance time; 5.0 makes an ambulance worth more than a few seconds of ordinary delay. See §4.3 for the why-chain. |
| 38 | Why divide by 40? | To keep rewards in a range Adam is comfortable with at lr 5e-4. | Un-normalised, a congested step gives delay in the hundreds of vehicle-seconds; the Q-targets would then be in the thousands and the first gradient steps would be enormous. 40 is `reward.normalize` in the config — a value, not code. |
| 39 | Why penalise switching at all — is that not fighting the objective? | Every switch costs 5 s of yellow-plus-all-red where nobody moves. Without a price on it, the agent flickers and throws away capacity. | 0.20 per switch against a delay term of order 1–10 makes it a nudge, not a veto. Set it to zero and you get oscillation; set it high and you get a fixed-time controller. |
| 40 | Why is pressure in the reward when it is already what max-pressure optimises? | It is a shaping term that gives the agent a reason to serve an imbalanced approach before it becomes a delay problem. | Weight 0.30 versus 1.0 on delay — delay is the objective, pressure is guidance. It also means our reward is not blind to the quantity our strongest baseline optimises. |
| 41 | Is the reward shared or individual? | Individual — each agent gets its own junction's reward. | With shared parameters and pooled replay, the *network* still learns from all four experiences, so there is implicit cooperation without a shared reward. A global shared reward would worsen credit assignment: an agent could not tell whether the improvement was its own doing. |
| 42 | Could the reward be gamed? | Partly. Never switching is limited by `max_green_s: 60`, which forces the change to start — but the FSM accepts a new request during amber/all-red, so a policy can cancel the change and hold the same phase. **(1.1.0, corrected)** | The honest answer: the delay term in the reward (not the FSM) is what makes starving a side street costly. The shipped policy cancels about one change in five mid-clearance; side streets waited up to ~113 s. Fix: ignore requests during clearance in `phases.py` — left as is because the published numbers depend on it. |

### D. DQN, Double, Dueling, PER, target network (19)

| # | Question | Say this | If pushed |
|---|---|---|---|
| 43 ★ | What is DQN in one sentence? | A neural network that approximates Q(s,a), trained on replayed transitions towards a bootstrapped target from a slowly-updated copy of itself. | The three ingredients that made it work in 2015 were the replay buffer (decorrelates samples), the target network (stabilises the regression target), and gradient clipping. We use all three. |
| 44 ★ | Why Double DQN — what bias does it remove? | Plain DQN takes a max over noisy estimates, and the max of noise is biased upward, so it systematically overestimates. Double DQN splits the decision from the valuation. | `y = r + γ·Q_target(s', argmax_a' Q_online(s',a'))`. The online net *chooses*, the target net *values*. Because their errors are not perfectly correlated, the optimism cancels. In signal control that matters: overestimating "hold green" gives you a starved side street. → *"Show me the line."* It is the target computation in `agents/double_dqn_agent.py`. |
| 45 ★ | What does the Dueling architecture add? | It splits the head into "how good is this state" and "how much better is each action", then recombines them. | `Q(s,a) = V(s) + (A(s,a) − mean_a A(s,a))`. Subtracting the mean advantage makes the decomposition identifiable — otherwise you could add a constant to V and subtract it from A. It helps here because in most states holding and switching are nearly equal in value, so learning V once is more efficient than learning two near-identical Q values. |
| 46 | Why subtract the mean advantage instead of the max? | The mean is the more stable choice and is what the paper recommends in practice. | With max-subtraction the identity is exact (Q(s,a*) = V(s)) but the gradient concentrates on one action; mean-subtraction spreads it and trains more smoothly. |
| 47 ★ | Explain prioritized experience replay. | Sample transitions in proportion to their TD error, so the agent revisits the experiences it is worst at predicting. | Priority p = \|TD\| + ε, sampling probability ∝ p^α with α = 0.6, and importance weights (1/N·1/P)^β with β annealed 0.4 → 1.0 over 20,000 steps to undo the induced bias. A sum-tree gives O(log N) sampling and update. Implementation in `agents/replay.py`, unit-tested. |
| 48 | Why α = 0.6 and not 1.0? | α = 1.0 is pure greedy prioritisation and overfits a handful of transitions. 0.6 interpolates towards uniform. | α = 0 recovers uniform replay. 0.6 is the paper's proportional-variant value and we did not need to tune it. |
| 49 | Why does β anneal upward? | Prioritised sampling biases the gradient; importance weights correct it. Early on we tolerate bias for speed, later we insist on correctness. | β = 1 gives the fully unbiased estimator. Annealing to 1 near the end means the final policy is trained on properly weighted updates. |
| 50 | What does the target network actually stabilise? | Without it you regress towards a target that moves every time you update — the classic chasing-your-tail divergence. | Hard sync every 500 learner steps (`target_update_every`). A soft Polyak update would be the alternative; hard copies are simpler and the paper's default. |
| 51 | Why Huber loss instead of MSE? | One gridlocked intersection produces a huge TD error; MSE would let that single sample dominate the batch. | Huber is quadratic near zero and linear beyond, so gradients are bounded. In the NumPy backend we clip the TD error to ±1 directly, which is the same effect. |
| 52 | Batch 64, buffer 50,000, min_buffer 1,000 — justify each. | 64 is a stable gradient estimate at low cost; 50,000 transitions is about 200 episodes of memory; 1,000 stops learning from an empty buffer. | 112 episodes × 240 steps × 4 agents ≈ 107,000 transitions pooled, so the buffer holds roughly the most recent half of training — recent enough to reflect the current policy, long enough to keep hard congested transitions around. |
| 53 | Learning rate 5e-4 — how did you pick it? | It is the standard DQN-family value and it converged, so we did not spend budget tuning it. | Honest answer: no systematic sweep was run — **a hyperparameter search cannot be evidenced from this project**. The training curve shows convergence, which is the evidence I have. |
| 54 | Hidden layers [128, 128] — why not deeper? | 23 inputs and 2 outputs. Two layers of 128 is already ~20,000 parameters for a small problem; depth would overfit and slow inference. | Exactly 19,971 parameters, verified. Inference is a couple of matrix multiplies — microseconds, which matters if this ever runs on junction hardware. |
| 55 ★ | You have no PyTorch here. What trained the model? | The from-scratch NumPy network in `agents/net.py`, with manual forward and backward passes and a hand-written Adam. | It implements the same dueling architecture and the same update. Both nets share one portable checkpoint format — a dict of NumPy arrays — so a model trained with either loads under the other. That is a graded feature, not a workaround. → *"Show me the backprop."* `NumpyQNet.backward` in `agents/net.py`. |
| 56 | Writing your own backprop is risky. How do you know it is right? | Because `test_replay.py` checks the forward and backward shapes and gradient behaviour, and because the resulting policy actually learns — a broken gradient does not take 178 s down to 41.6 s. | A stricter check would be finite-difference gradient verification against the torch path; that **is not implemented**, and it is a fair criticism. |
| 57 | What is the exploration schedule, precisely? | Linear ε from 1.0 to 0.05 over 22,000 control steps, then flat. | Shared across all agents via a single `EpsilonGreedy` object, so exploration is synchronised. It has a `state_dict`, which is how the step counter survives into the checkpoint — that is how I proved the checkpoint came from a 112-episode run. |
| 58 | How often do you update the network? | Every control step (`train_every: 1`) once the buffer has 1,000 transitions. | With shared parameters that is one update per env step from a pooled buffer containing all four agents' transitions — effectively 4× the data per update compared with independent nets. |
| 59 | What is catastrophic forgetting and does it bite you? | It would if we trained the scenarios in sequence. The curriculum *cycles* them, so the buffer always holds a mix. | That is the reason for cycling rather than sorting: `scenarios[episode % 4]`. It keeps the replay distribution balanced so the final policy handles all four densities instead of overfitting the last one seen. |
| 60 | Did you compare against vanilla DQN to prove Double+Dueling+PER helps? | No. **That ablation was not run and cannot be evidenced from this project.** | The honest framing: those components are established improvements with published ablations, and our contribution is the system, not a claim about them. Running the ablation is a concrete next experiment — four training runs, a few hours. |
| 61 | What is the loss value telling you? | Around 0.78–1.19 in the final episodes. Nothing on its own — Q-learning loss does not converge to zero. | It is a moving-target regression, so the loss tracks how fast the target is changing. Judge learning by the reward and the wait time, not the loss. Quoting a low loss as evidence of a good policy is a trap. |

### E. Multi-agent design (13)

| # | Question | Say this | If pushed |
|---|---|---|---|
| 62 ★ | Is this really multi-agent if the four agents share one network? | Yes. Four agents each observe their own junction and each choose their own action independently at every step. They share *weights*, not decisions. | This is shared-parameter independent Q-learning, a standard cooperative-MARL setup for homogeneous agents. The alternative is in the code: `share_parameters: false` builds four separate networks with four buffers. → *"Then how is it different from one agent controlling everything?"* A single joint agent would need a 2⁴ = 16-action space and the full global state, and it would not scale. Ours is decentralised at execution. |
| 63 ★ | Why share parameters at all? | The four intersections are structurally identical, so one network sees 4× the training data. It is a sample-efficiency decision. | It also means the model size is independent of grid size — a 3×3 grid uses the same 19,971 parameters. The cost is that a genuinely heterogeneous junction (different geometry, different phase count) could not share; that is what the independent mode is for. |
| 64 | How do the agents coordinate without communicating? | Through observation. Each one sees its neighbours' pressure and current phase in its own state vector. | It is coordination by shared observability rather than by message passing. No protocol, no latency, no single point of failure — and it is what a real junction could sense from its neighbours' controllers. |
| 65 | Is this cooperative or competitive? | Cooperative in intent. Each agent has its own reward, but reducing your queue reduces what you send your neighbour. | It is not a zero-sum game: there is no fixed pot of green time being fought over across junctions. The shared-parameter setup makes it structurally cooperative because one policy serves everyone. |
| 66 | What is the non-stationarity problem in MARL? | From any one agent's view, the environment keeps changing because the other agents are learning too. | Shared parameters mitigate it — all four agents change together, so the policy an agent is adapting to is its own. That is a real advantage of the design, not just a convenience. |
| 67 ★ | What is QMIX and why is it off? | QMIX is centralised training with decentralised execution: a mixing network combines per-agent Q values into a joint value, constrained to be monotone in each agent's Q. It is implemented, disabled, and **produced none of our results**. | `qmix.enabled: false`, and it requires PyTorch, which the shipped run did not have. With 4 homogeneous agents and neighbour observations, shared-parameter IQL was sufficient. It is scaffolding for the next stage — I will not claim it as a delivered result. |
| 68 | Why would QMIX help if you enabled it? | It learns a joint value function, so it can represent coordination that independent learners cannot — like sacrificing one junction's delay for a corridor-wide gain. | The monotonicity constraint (∂Q_joint/∂Q_i ≥ 0) is what keeps decentralised argmax consistent with the joint argmax. The trade-off is that it needs a centralised critic during training. |
| 69 | What is the credit-assignment problem across agents? | If the corridor improves, which junction earned it? | We sidestep it with individual rewards: each agent is scored on its own junction, so the signal is unambiguous even if it is myopic. QMIX or COMA would address it properly with a joint value and counterfactual baselines. |
| 70 | How would this scale to 100 junctions? | The model does not grow — shared parameters and a fixed 23-D observation. Training time and coordination difficulty do grow. | The realistic limit is not the network but the credit assignment and the fact that a 4-neighbour summary is a weak proxy for a large network's state. A graph neural network over the junction topology is the principled scaling answer. |
| 71 | Do all agents act simultaneously? | Yes. `act()` returns one action per agent from the same observation dict, then `step()` applies them all. | Simultaneous, not turn-based — which is what makes it a Markov *game*. Sequential decision-making would be easier to coordinate and less realistic. |
| 72 | What if one junction's controller fails? | The others keep running; each one's policy only needs its own observation, and a missing neighbour reads as zero. | That graceful degradation is a direct consequence of decentralised execution and boundary masking. It is a genuine engineering argument for this architecture over a central optimiser. |
| 73 | Do the four agents ever disagree in a way that hurts? | They can. Two adjacent junctions can both hold their arterial green and create a platoon that arrives at a red. | The neighbour phase feature is exactly what lets the policy learn to avoid that. How often it still happens **cannot be quantified from the current logs** — measuring it would need per-vehicle stop counts, which the point-queue backend does not record. |
| 74 | Where is the multi-agent code? | `agents/multi_agent.py` — 139 lines. It owns the learner, dispatches `act`/`observe` over agent-id dicts, and handles the portable save/load. | It is also where the shared-vs-independent switch lives, and where the NumPy-2/NumPy-1 checkpoint compatibility shim sits. |

### F. Safety layer (9)

| # | Question | Say this | If pushed |
|---|---|---|---|
| 75 ★ | How do you guarantee the light never shows conflicting greens? | The agent cannot set the light. It requests a phase; `SignalFSM` decides. Yellow and all-red are inserted by the machine, not chosen by the policy. | `active_green()` returns the **empty set** during YELLOW and ALL_RED, so during clearance every approach is red. A request arriving before `min_green` is discarded. Conflicting greens are unreachable states, not unlikely ones. |
| 76 ★ | Prove it. | `test_phases_safety.py` fires thousands of random phase requests at the FSM and asserts no two conflicting approaches are ever green together, plus that min and max green hold. | It is a property test over random action sequences rather than a formal proof. A model checker over the FSM would be the stronger claim, and it is a fair thing to ask for. |
| 77 | Why 3 s yellow and 2 s all-red? | Standard practice: amber lets vehicles in the dilemma zone clear, all-red lets the intersection empty before the conflicting movement starts. | Both are config values (`yellow_s`, `all_red_s`). Real amber timing is a function of approach speed and grade; 3 s is typical for a 50 km/h approach. Changing them is a config edit, not a code change. |
| 78 | What does min_green protect against? | Flicker. Without it a wait-minimising policy would switch every decision step and nobody would ever get through. | 10 s is roughly the time for a standing queue to start moving and discharge a few vehicles. It also caps how often the switch penalty can be incurred. |
| 79 ★ | What does max_green guarantee? | That no green runs longer than 60 s: the FSM starts a change regardless of the policy. **(1.1.0, corrected)** It does not, on its own, guarantee the side street gets served. | The FSM accepts a new request during amber and all-red, so the policy can cancel the change and return to the same phase; with the shipped policy the longest side-street wait reached ~113 s. A one-line fix (ignore requests during clearance) would make it a real guarantee, at the cost of re-running every benchmark. |
| 80 | What if the neural network returns NaN? | The FSM still only accepts a valid phase index, and `argmax` of NaNs still returns an index in range, so the light stays legal — it would just make a poor choice. | The observation builder is unit-tested to return finite values. A production system would add an explicit sanity gate and a fallback to a fixed plan on any anomaly; that is not implemented here. |
| 81 | Is the safety layer learned or hardcoded? | Hardcoded, deliberately. Safety you can prove beats safety you hope was learned. | It is the clean separation the project is built around: the environment owns safety, the agent owns optimisation. It is also what would let a certified controller host the policy as an advisory layer. |
| 82 | Does the FSM ever ignore an emergency preemption request? | It honours the requested phase but still inserts yellow and all-red on the way. It will not slam a green on. | So preemption is fast but never unsafe. That is why clearance improves by 1.3–3.5× rather than instantly. |
| 83 | Where does the 5 s clearance cost show up in the numbers? | In the switch penalty and in throughput. Every phase change spends 5 s where nothing moves. | With `max_green 60 s`, worst case you lose about 8 % of capacity to clearance. It is also why an unpenalised switching policy performs badly — the loss compounds every 5 s. |

### G. Emergency-vehicle preemption (8)

| # | Question | Say this | If pushed |
|---|---|---|---|
| 84 ★ | How does emergency preemption work? | `control/emergency.py`, 63 lines. Every control step it scans all four approaches of every junction; if an emergency vehicle is present it finds the phase that serves that approach and *requests* it on that junction's FSM. | It is a deliberately rule-based override, matching how real systems (Opticom-style) sit on top of the normal controller rather than replacing it. It wraps *any* base controller — RL, fixed-time, max-pressure. |
| 85 ★ | Is preemption learned or scripted? | Scripted, and I would defend that choice. You do not want an ambulance's right of way to depend on a converged neural network. | The learned part is complementary: `w_emergency = 5.0` rewards the agent for clearing emergencies, so the policy is emergency-aware even before the override fires. Two mechanisms, one hard and one soft. |
| 86 ★★ | Then the emergency improvement is just your override, not the RL. | No — and this is the important detail. **All three controllers receive the identical override.** `run_controlled_episode` attaches the `EmergencyController` regardless of which controller is running, and the benchmark never disables it. | So the 1.30×/1.56×/1.92×/**3.48×** clearance speedups are what remains *after* equalising preemption. The gap comes from the RL controller having left the corridor less congested for the ambulance to drive into, which is why the advantage grows with density. |
| 87 | How is clearance time measured? | Total time in network for the emergency vehicle: injected at t = 720 s (40 % of the 1800 s episode), timed until it exits the far side of the grid. | It enters at `J1_0` from the west and runs straight east across the middle row — two junctions, one 200 m link, no turns, speed factor 1.3. The free-flow floor is about 11 s of transit plus discharge, so RL's 20 s at low density is close to unimprovable; fixed-time's 113.7 s at rush is almost all queueing. |
| 88 ★ | How many emergency vehicles per episode? | Exactly one. So each cell of the clearance table is **three samples** (one per seed), not a distribution. | That is the honest weakness of that metric, and I would flag it before an examiner does. The effect size at rush (113.7 → 32.7 s) is far larger than the seed spread, so the direction is safe; a tighter claim would need many injections per episode. |
| 89 | How is the emergency detected? | `backend.emergency_present(tls_id, approach)` — the simulator is asked directly. | It is an oracle, and I will say so. A real deployment uses transponder, GPS/CAD feed, or siren detection, all of which have false negatives and range limits. Modelling detection latency is listed as a Level-2 improvement. |
| 90 | Can preemption make the light unsafe? | No. It calls `fsm.request(phase)`, exactly the same entry point the agent uses. Yellow and all-red are still inserted. | That is why clearance improves by a factor rather than instantly — safety costs the ambulance up to 5 s per junction, and I would not trade that away. |
| 91 | Does the emergency bonus work on the SUMO backend? | No. `SumoBackend.pop_emergency_cleared()` returns 0 — SUMO's arrival list is not filtered by vtype — so `w_emergency` only fires on the built-in backend. | The override itself works on both; only the reward term is backend-specific. Since all shipped training and evaluation ran on the built-in backend, no reported number is affected — but it is a real gap and it is documented in the module docstring rather than hidden. |

### H. Baselines and fair comparison (7)

| # | Question | Say this | If pushed |
|---|---|---|---|
| 92 ★ | What are you comparing against? | Two baselines. Fixed-time: a standard 30 s + 30 s cycle, 60 s total, no sensing. Max-pressure: a published adaptive algorithm that each step serves the phase with the greatest queue pressure. | Comparing only against fixed-time would be a soft target — any adaptive method beats a clock. Max-pressure is the honest bar because it is provably throughput-optimal under its own assumptions. |
| 93 ★★ | Max-pressure beats your RL at low and medium density. Explain. | It does: 19.22 s vs 22.31 s at low, 22.52 s vs 25.24 s at medium. Max-pressure is greedy and near-optimal when there is slack, and it pays no switching penalty. Our reward charges 0.20 per switch and the policy is trained across all four densities, so it is deliberately less twitchy. | The ordering reverses where it matters: 35.56 vs 35.17 at high, and 50.54 vs **43.89** at rush — a 13 % win over max-pressure when the network is saturated, which is the regime the project targets. Max-pressure also needs the full queue vector every step and has no notion of clearance cost or emergencies. I would rather report the reversal than hide the two cells I lose. |
| 94 | Is max-pressure a strong implementation or a strawman? | It is the standard rule: for each phase compute upstream minus downstream queue, take the max, respect the same min/max green and clearance FSM the RL agent obeys. `control/max_pressure.py`. | It runs on the identical environment, identical seeds, identical safety constraints, and it gets the same emergency override. If anything it is favoured, because it reads exact queues with no sensing noise. |
| 95 ★ | How do you know the comparison is fair? | Same seed, same environment object, same arrival sequence, same safety FSM, same preemption. Only the `act()` implementation differs. | Seeding is derived per stream via `derive_seed(base, *tags)`, so controller A and controller B on seed 0 face a bit-identical demand realisation. I reran all 36 rows this week and every `avg_waiting_time` reproduced to 1e-9. |
| 96 | Why not compare against SCATS or SCOOT? | They are proprietary commercial systems; there is no reference implementation I could run inside this project, and reproducing one from the literature would be a project in itself. | I would not claim a comparison I did not run. Max-pressure is the recognised research baseline for this task and it is the one I implemented and measured. |
| 97 | Is the fixed-time baseline realistic? | It is honest but not optimised — a symmetric 30/30 split on a corridor that carries 2.2× more east–west demand. | A traffic engineer would tune that split offline (Webster's method) and close part of the gap. I would concede that immediately, and point out that this is exactly the argument *for* adaptive control: the fixed plan is only right for the demand it was tuned to, and the rush column shows what happens when demand moves. |
| 98 | Did you compare against another RL method? | No. **No RL-vs-RL ablation was run and cannot be evidenced from this project.** | The comparisons I can defend are RL vs fixed-time and RL vs max-pressure, over 4 densities × 3 seeds. Vanilla-DQN and no-PER ablations are the first two experiments I would run next, and they are listed as Level-1 improvements. |

### I. Training (10)

| # | Question | Say this | If pushed |
|---|---|---|---|
| 99 ★ | Walk me through your training run. | 112 episodes, each 1200 simulated seconds = 240 control steps, so **26,880 control steps** total. Every episode picks a scenario from the curriculum, runs with ε-greedy exploration, stores transitions in prioritized replay, and takes one gradient step per control step. | The whole run is logged in `outputs/logs/train.csv` — 112 rows, columns `episode, scenario, steps, ep_reward, avg_wait, avg_queue, throughput, epsilon, loss, seconds`. Every row shows `steps = 240`, which is where the 26,880 comes from. |
| 100 ★★ | How long did training take? | **33.4 seconds of wall time** for all 112 episodes — the sum of the `seconds` column. | That surprises people, so I lead with the reason: the built-in backend is a point-queue model, not a car-following microsimulation, and the network is a 19,971-parameter NumPy MLP. It is honest about what it buys me — fast iteration — and honest about what it costs: no lane changes, no acceleration profiles. On SUMO the same run would be orders of magnitude slower. |
| 101 ★ | What is the curriculum, exactly? | Four densities cycled round-robin: `scenario = curriculum[ep % 4]`, so low/medium/high/rush each get exactly 28 episodes, interleaved. | I would be precise here rather than let the word oversell: this is **density cycling, not progressive difficulty**. A true curriculum would train low first and ramp. Cycling was chosen so the single shared policy stays competent at every density instead of forgetting the easy regime — and the per-scenario curves show it worked at all four. |
| 102 | Does the agent see the same traffic every episode? | No. The seed is `derive_seed(42, "train", scenario, ep)`, so all 112 episodes have distinct arrival realisations. | That is what stops it memorising one demand trace, and it is also why the training curve is jagged rather than smooth — each point is a different problem instance. Evaluation then uses a *different* seed namespace (0, 1, 2), so no evaluation episode was trained on. |
| 103 ★ | How much did it actually improve during training? | Compare like scenario with like. Mean `avg_wait` over the first 7 versus last 7 episodes of each density: low 48.7 → 24.0 s, medium 61.9 → 27.6 s, high 113.5 → 38.7 s, rush 160.7 → 45.7 s. | Episode reward improves in step: high-density mean reward −4156 → −1485, rush −6780 → −1918. Throughput also climbs where it matters (high 1104 → 1431 completed trips, rush 1245 → 1456), so it is not buying wait reductions by refusing to admit vehicles. |
| 104 ★ | Why is episode reward not monotonically increasing in your log? | Because consecutive episodes are *different scenarios*. Episode 0 is low (−924), episode 2 is high (−6699). The scenario dominates reward magnitude. | Reward is only comparable within a density, which is exactly how I report it above. This is the single most common misreading of that CSV and I would pre-empt it. |
| 105 ★★ | Your loss goes **up** — 0.04 early, 1.19 at the end. Is training broken? | No, and this is expected in DQN rather than something to explain away. The target is bootstrapped from a network that keeps changing, and PER deliberately feeds the batch high-error transitions. Loss is not a progress metric here. | Two further details: the logged value is the *last* loss of the episode, not the mean, so it is noisy; and episode 0 logs 0.0 because the buffer had not reached `min_buffer = 1000` yet. Progress is measured by `avg_wait` and reward per scenario, both of which improved by roughly 2×. |
| 106 | When did exploration stop? | ε decays linearly 1.0 → 0.05 over 22,000 control steps, so it hits the floor **during episode 91** of 112 — the log first shows 0.05 at episode 91. | The last ~20 episodes are therefore near-greedy fine-tuning. ε never reaches 0 on purpose: a little exploration keeps the replay buffer from collapsing onto one trajectory. |
| 107 | How was the shipped model chosen? | Greedy evaluation every 10 episodes; the best-scoring weights are saved. The shipped file carries the internal tag `ep70_wait27.0` — best greedy score was 27.0 s at episode 70 — and its ε counter reads exactly 16,800 = 70 × 240, so the tag and the counter corroborate each other. | Selection averages low/medium/high and **excludes rush**, because rush is near-saturated for every controller and its variance would dominate the selection signal — the agent still *trains* on rush. The final-episode weights are kept separately as `atsc_2x2_final.pt` (ε counter 26,880 = 112 × 240). That the best model is not the last model is normal, and I would rather state the selection rule than imply the last epoch was best. |
| 108 | How full is the replay buffer at the end? | Four agents × 26,880 steps = **107,520 transitions** through a 50,000-capacity buffer, so it wrapped about 2.1× and the final buffer holds roughly the last 52 episodes of experience. | Consequences worth owning: early exploratory transitions are gone by the end, and the target network hard-synced **53** times (26,631 learner steps ÷ 500). Both are deliberate — recency helps once the policy is decent. |

### J. Evaluation, metrics and results (9)

| # | Question | Say this | If pushed |
|---|---|---|---|
| 109 ★ | Describe your evaluation protocol. | 3 controllers × 4 densities × 3 seeds = **36 episodes**, each 1800 simulated seconds, 30 s warm-up discarded, one emergency vehicle injected at t = 720 s. Every row is in `outputs/benchmark_results.csv`. | Identical seeds across controllers, so each controller faces a bit-identical arrival sequence. I reran all 36 rows from the shipped code and checkpoint this week and every `avg_waiting_time` matched the CSV to 1e-9. |
| 110 ★ | Only 3 seeds? | Yes, and I will not oversell it. Three seeds gives a direction, not a confidence interval. | What defends the headline is effect size versus spread: at rush, RL's advantage is 69 s while the *entire* seed range is 2.69 s for RL and 30.02 s for fixed-time. At low density, where my margin is 4 s, three seeds is genuinely too few and I would say so. Ten seeds costs about a minute of compute — it is on the improvement list for exactly that reason. |
| 111 ★ | How is average waiting time defined? | Total accumulated stopped time divided by the number of vehicles counted — and vehicles **still in the network at episode end are folded in** via `record_present_wait`. | That last part matters: if I only counted completed trips, a controller could look good by stranding its worst vehicles forever. Including them makes the metric un-gameable in that direction. It is per vehicle, not per intersection. |
| 112 ★★ | Your deck claims +35–45 % throughput. Your data shows +0.1 % to +7.6 %. | The deck target was not met and I would correct it rather than defend it. Measured throughput change versus fixed-time is −0.1 % (low), +0.0 % (medium), +2.8 % (high), +7.6 % (rush). | The reason is structural: throughput is bounded by *demand*, and demand is exogenous — the same arrival process feeds every controller. In under-saturated conditions every controller eventually discharges nearly every vehicle, so throughput cannot separate them; it only moves once queues start spilling, which is why the gain appears at high and rush. Waiting time and queue length are the metrics with headroom, and there RL wins by 48.8 % and 61.2 %. |
| 113 ★★ | You report a 108 % improvement in "average speed". That cannot be real. | Correct to be suspicious. `avg_speed` in this project is **not a measured speed** — it is `free_speed × (moving vehicles / vehicles present)`, i.e. a moving-fraction proxy scaled to m/s. | So a doubling means the fraction of vehicles in motion roughly doubled, which is a real and meaningful statement about congestion — but it is not a speedometer reading, and calling it "average speed" is the least defensible label in my metric set. The point-queue backend has no continuous speed to average, so a true figure would require the SUMO backend. I would rename it `moving_fraction` in the next revision. |
| 114 ★ | Are your fuel and CO₂ numbers real? | No — they are a linear proxy: 0.30 ml/s per halted vehicle plus 0.90 ml/s per moving vehicle, and CO₂ = fuel × 2.31 g/ml for petrol. | Because it is a fixed linear function of the halted and moving counts, it carries **no independent information** beyond the queue metrics — the −42.5 % at rush is the queue result restated in different units. I include it because reduced idling is the real-world justification for the project, but I would never present it as an emissions measurement. SUMO's HBEFA emission model is the credible route. |
| 115 ★ | What is your single strongest result? | Not the average — the *consistency*. Seed-to-seed spread in average waiting time at rush: fixed-time 30.02 s, RL 2.69 s. About **11× more consistent** under saturation. | A traffic authority cares as much about predictability as about the mean, and a fixed plan is fragile precisely when the network is loaded. That result also arrives free — it is a property of adaptivity, not something I tuned for. |
| 116 | Why does the improvement grow with density? | Because a fixed plan is only correct for the demand it was tuned for. At low density there is enough slack that any reasonable plan works; at saturation, green time allocated to an empty approach is capacity you can never recover. | Numerically: −15.1 % at low, −26.5 % medium, −48.8 % high, −61.2 % rush. That monotone trend is the most convincing shape in the whole results set, because it matches the theory rather than just looking good. |
| 117 | Which config keys govern the evaluation? | `eval.controllers`, `eval.scenarios`, `eval.seeds: [0,1,2]`, `eval.inject_emergency: true`, and the episode length. | One precision point I would volunteer: the environment's episode length actually comes from `sim.episode_seconds`, while `eval.episode_seconds` is consumed by the SUMO route generator and the summary header. Both are 1800, so every shipped number is consistent — but they are two keys, and a careful reader will notice. |

### K. Simulator, backends and determinism (7)

| # | Question | Say this | If pushed |
|---|---|---|---|
| 118 ★★ | Did you use SUMO or not? Be precise. | The SUMO backend is fully implemented and selected automatically when SUMO is installed. **Every number in this report was produced by the built-in backend.** I will not imply otherwise. | `SumoBackend` (168 lines) drives TraCI: `netgen.py` writes nodes and edges, `netconvert --tls.guess` derives the connections and a default light program, and the backend then overrides the light every step with `setRedYellowGreenState`. It shares the identical `SimBackend` interface, so switching is a config value — but switching would change the numbers, and I have not re-measured on SUMO. |
| 119 ★ | What exactly does your built-in simulator model? | A point-queue, store-and-forward network. Poisson arrivals at each boundary approach, a queue per (junction, approach), discharge at saturation flow while green, then a fixed link travel time to the next junction. | Concretely: 0.5 veh/s per green lane implemented as a credit accumulator, so one vehicle every 2 s; a 200 m link at 13.9 m/s is 14.4 s of transit, which is what makes green-wave coordination worth learning; routes are precomputed straight corridors with a 10 % chance of one turn, so every trip terminates. |
| 120 ★★ | What does it *not* model? | Four things I would list before being asked: **no spill-back** — queues are unbounded, so a jammed approach never blocks the junction upstream; no car-following or acceleration; no lane changing or protected turn lanes; and no true per-vehicle speed, only a moving fraction. | Spill-back is the most consequential omission, and it cuts against me in a specific way: gridlock is the failure mode adaptive control is most valuable for, and my simulator cannot produce it. So the rush-hour advantage is measured in a world that is *kinder* than reality to the fixed-time baseline in some respects and harsher in others. Honest position: the direction of the result is safe, the magnitude is simulator-specific. |
| 121 | Why build a simulator instead of just using SUMO? | Two reasons, one principled and one practical. RL needs a very large number of samples, and the point-queue model runs 112 episodes in 33 seconds. And the project has to install and run on any machine with Python and NumPy — a demo that needs a SUMO install is a demo that fails on the day. | The architecture is what protects the claim: both backends implement the same interface and return the same metric schema, so no environment, agent or evaluation code knows which one it is talking to. The shortcut is contained in one file. |
| 122 ★ | How do you guarantee reproducibility? | Every random stream is derived from one seed via `derive_seed(base, *tags)` — sha256 of the tag tuple, top 32 bits — so arrivals, routes and exploration are independent, named streams rather than one shared generator. | The proof is concrete: I reran all 36 benchmark rows from the shipped code and checkpoint and every `avg_waiting_time` matched the CSV to within 1e-9. There is also a unit test, `test_determinism_same_seed`. |
| 123 ★ | Has that ever broken? | Yes, and it is worth telling. Adding vehicle *types* for the dashboard drew from the main RNG stream, which shifted every subsequent arrival and silently changed the KPIs. | The fix was to give cosmetic draws their own stream (`_kind_rng`), so a purely visual feature cannot move a reported number. That is now stated in the function's docstring. I would rather present this as a lesson about seeding discipline than pretend the first version was right. |
| 124 | Which backend does the dashboard use, and why? | The built-in one, forced by `dashboard.backend: mini`. | A live demo needs a smooth 200 ms tick and instant reset; launching SUMO per session would make it fragile in front of an audience. The dashboard is a demonstration surface, not a measurement instrument — the measurements come from `run.py eval`. |

### L. Engineering, dashboard and tests (6)

| # | Question | Say this | If pushed |
|---|---|---|---|
| 125 ★ | How is the code organised? | 47 Python files, 5,463 lines, in seven packages: `sim` (backends), `envs` (environment + safety FSM + observation), `agents` (learner, nets, replay), `control` (three controllers + preemption), `train`, `eval`, `dashboard`. `config.yaml` is the single source of truth. | The design rule is that dependencies point inwards to `sim/backend.py`, which is the most imported module in the project (12 importers) and defines the abstract interface plus the metrics accumulator. That is what makes SUMO ↔ built-in interchangeable. |
| 126 ★ | What does the dashboard actually do? | Runs two environments side by side — RL and a baseline — on the same seed, advanced by a background thread every 200 ms, and streams state to a browser. Seven live controls: play, pause, step, reset, speed, scenario, inject emergency. | The front end is 959 lines of hand-written HTML, CSS and JavaScript with **zero third-party libraries** — the junction rendering *and* the live line charts are drawn directly on Canvas. No CDN request anywhere, which is why it works with the network unplugged. That was a deliberate constraint, not an omission. |
| 127 ★ | Is the dashboard secure? | Not in the sense a production service would be, and I can be precise about why that is acceptable here. `POST /api/cmd` is unauthenticated, so anyone who can reach the port can change the scenario or reset the run. | Locally it binds `127.0.0.1`, so the exposure is local. The public instance described in `docs/DEPLOYMENT.md` is deliberately open, because the attack surface is a simulation: no account, no upload, no database, no filesystem write, and nothing persisted between restarts. Before this became anything but a demo it would need auth on `/api/cmd`, a rate limit, an origin check and one session per visitor. I list it as a known weakness rather than waiting to be caught by it. |
| 128 ★ | What do your tests cover? | **(1.2.0)** 195 tests in 15 files: 43 for the hardware model (protocol, firmware logic compiled for the PC, real-time playback, the simulated board end to end); **(1.1.0)** 144 tests in 13 files: the original 26 below, plus the dashboard protocol (a delta stream rebuilds every keyframe; the browser's between-frame picture equals the simulator second by second; the JavaScript model matches its Python mirror), the FastAPI and stdlib servers (WebSocket, path traversal, validation), the vehicle catalogue, and checkpoint/config compatibility. Original 26 tests in 6 files, all passing. Safety FSM (5), controllers including preemption (5), PER sum-tree and the NumPy net (5), environment API and determinism (4), reward shaping (3), benchmark aggregation maths (4). | Two are behavioural rather than unit tests, which I think is the more valuable kind here: `test_max_pressure_beats_fixed_on_high` pins the baseline ordering, and `test_numpy_qnet_learns_fixed_target` proves the from-scratch network genuinely learns by regressing it onto a fixed target. |
| 129 ★★ | What is *not* tested? | Named plainly: no test of the SUMO backend — it cannot run without SUMO installed; no test of the dashboard HTTP layer or the JavaScript; no end-to-end training test; and no test asserting the shipped checkpoint's numbers. | Coverage is honest about where it stops. The gap I would close first is a smoke test that loads `atsc_2x2.pt` and asserts one benchmark row within tolerance — that turns the reproduction I ran by hand into something CI enforces. `run.py doctor` currently does the checkpoint-loads part. |
| 130 | Why no PyTorch, FastAPI or SUMO on the machine that produced the results? | Because the fallbacks are the point. The project ships a NumPy Q-network, a stdlib HTTP server and a built-in simulator so that it runs on a bare Python install. | That is also the strongest evidence they are real fallbacks and not decorative `try/except` blocks — the shipped model was trained by the NumPy network, and the checkpoint format is deliberately a portable dict of arrays so the same file loads under PyTorch unchanged (`test_numpy_qnet_weight_portability`). |

### M. Trick, trap and pressure questions (8)

These are the questions designed to catch an overclaim. In every case the winning move is to concede precisely and immediately.

| # | Question | Say this | If pushed |
|---|---|---|---|
| 131 ★★ | Is this deep reinforcement learning or just deep learning? | Deep RL. There are no labels anywhere — the network learns from a scalar reward produced by the environment's own dynamics, via temporal-difference bootstrapping. | If it were supervised learning I would need a dataset of correct signal decisions, which does not exist. The "deep" part is only the function approximator; the learning signal is the Bellman target. |
| 132 ★★ | Your reward uses waiting time and you report waiting time. Isn't that circular? | It is the same quantity, and I would rather explain the distinction than deny it. Optimising the objective you measure is what optimisation *is*; the honest question is whether I also report things I did not optimise. | I do: throughput, emergency clearance, moving fraction and seed stability are all outside the reward, and the first of those (+0.1 % to +7.6 %) is the weakest result in the set — which is exactly why leaving it in matters. |
| 133 ★★ | Did you invent anything, or just assemble known parts? | I assembled known parts. Double DQN (2016), Dueling networks (2016), PER (2016) and max-pressure are all published; the contribution is the integrated system, the safety layer, the honest baseline comparison and the reproducibility. | I would not claim algorithmic novelty for a final-year project, and claiming it is the fastest way to lose an examiner's trust. What is mine is every line of the environment, the point-queue simulator, the from-scratch NumPy network with its own backprop, the multi-agent wrapper and the evaluation harness. |
| 134 ★★ | Would this work on a real road tomorrow? | No. It is trained and evaluated entirely in simulation, on synthetic Poisson demand, with an oracle view of queues and emergencies. | The gap list is specific: sensing noise and detector failure, spill-back, real turning movements, pedestrians, calibration against real counts, and a safety case for a certified controller. A credible path is shadow mode — run the policy alongside the existing controller and log disagreements before it is ever allowed to actuate. |
| 135 ★ | Show me where your policy is *worse* than a baseline. | Max-pressure beats it at low and medium density: 19.22 vs 22.31 s and 22.52 vs 25.24 s. And throughput at low density is 0.1 % *below* fixed-time. | Both are in the shipped CSV; neither is hidden. The trade is deliberate — a switch penalty and a policy trained across all four densities gives up a little in the easy regime to win 13 % over max-pressure at saturation. |
| 136 ★ | Does the agent choose the yellow duration? | No. The agent's entire action space is 2 — target phase North-South or East-West. Yellow and all-red are inserted by the FSM. | This is the question that catches people who oversell agent authority. The correct framing is that the agent proposes; the safety machine disposes. |
| 137 ★ | Is QMIX part of your results? | No. It is implemented, disabled in config, and requires PyTorch, which the shipped run did not have. **No reported result used it.** | I describe it as scaffolding for the next stage. Claiming a CTDE result I did not run would be the single easiest thing for an examiner to disprove — they only have to grep `qmix.enabled`. |
| 138 ★ | What is your accuracy? | Accuracy is not defined for this problem — there is no ground-truth correct action to be right or wrong about. | The metrics that do apply are average waiting time, queue length, throughput and clearance time, all versus two baselines on identical seeds. If the question means "how well does the network fit", the training loss is 0.78–1.19 late in training and rising, which in DQN is expected and not a quality measure. |

### N. Contribution, reflection and viva craft (6)

| # | Question | Say this | If pushed |
|---|---|---|---|
| 139 ★★ | What did *you* build versus what came from libraries? | I wrote the environment, the point-queue simulator, the safety FSM, the observation builder, the multi-agent wrapper, the prioritized replay with its sum-tree, the NumPy network including its backward pass, all three controllers, the preemption override, the training loop, the evaluation harness and the entire dashboard front end. The algorithms are published; the implementations here are mine. | Libraries used are NumPy, pandas and matplotlib, plus optional PyTorch, FastAPI/uvicorn and SUMO/TraCI. There is no RL framework — no Stable-Baselines, no RLlib, no Gym environment library. Only claim the parts you can open and explain on the spot. |
| 140 ★★ | What was the hardest part? | Making the results trustworthy, not making the agent learn. The reproducibility bug is the example: a cosmetic dashboard feature drew from the main RNG stream and silently moved every KPI. | That is a better answer than "tuning hyperparameters" because it is specific, it shows judgement, and it has a verifiable fix — separate named RNG streams, and 36/36 rows now reproducing to 1e-9. |
| 141 ★ | What would you do differently? | Three things: measure on SUMO before writing any number down, run 10 seeds instead of 3, and run the two ablations (no-PER, vanilla DQN) that would let me attribute the gain to the specific algorithm choices. | I would also rename `avg_speed` to `moving_fraction` — the current label is the most misleading thing in my metric set, and I found it by re-deriving every number from the CSVs rather than trusting the report. |
| 142 ★ | What did you learn that you did not expect? | That the most defensible result was the one I did not aim for: variance. RL's seed spread at rush is 2.69 s against fixed-time's 30.02 s. | It reframed how I think about the contribution — adaptivity buys predictability under load, which is arguably what an operator values more than a better mean. |
| 143 | If I gave you one more month, what would you do? | In order: SUMO re-measurement, 10 seeds with confidence intervals, the two ablations, then sensing noise. Only after that would I touch QMIX. | The ordering is deliberate — it is credibility work before capability work. Adding a fancier algorithm on top of a three-seed point-queue result would make the project look more advanced and be worth less. |
| 144 ★★ | Summarise your project in thirty seconds. | Four traffic lights, each an independent Double-Dueling-DQN agent with shared weights, seeing its own queues and its neighbours' pressure, choosing a phase every five seconds under a hard safety machine it cannot override. Against a fixed 60-second cycle it cuts average waiting time by 15 % in light traffic and 61 % at rush hour, clears emergency vehicles 3.5× faster under saturation, and is 11× more consistent seed to seed. All in simulation, all reproducible from the shipped code. | If they want one number: **61 % at rush**. If they want one caveat, give it before they ask: it is simulation only, on synthetic demand, and max-pressure still beats it when the roads are quiet. |

---

## 12. Twenty-five facts to memorise cold

If you know only these, you can survive any question in the bank by reasoning from them.
Every one is verified against the shipped code, `config.yaml` or `outputs/*.csv`.

| # | Fact | Value | Where it lives |
|---|---|---|---|
| 1 | Grid and agents | 2×2 = **4 signalised junctions = 4 agents**, 4 approaches each, 200 m links | `config.yaml → network` |
| 2 | Action space | **2** — target phase North-South or East-West. Nothing else. | `envs/phases.py` |
| 3 | Decision rate | one action every **5 s**; eval episode 1800 s = **360 control steps** | `signal.decision_interval_s` |
| 4 | Observation | **23 numbers** = 4 queues + 4 waits + 2 phase one-hot + 1 green-elapsed + 4 emergency flags + 8 neighbour (4 pressure, 4 phase) | `envs/spaces.py` |
| 5 | Network | 23 → 128 → 128 → dueling value + advantage heads; **19,971 float64 parameters** | `agents/net.py` |
| 6 | Safety timings | min green **10 s**, max green **60 s**, yellow **3 s**, all-red **2 s** | `config.yaml → signal` |
| 7 | Reward | −1.0·delay/40 − 0.30·pressure/40 − 0.20·switched + 5.0·cleared | `traffic_env._rewards` |
| 8 | Learner | Double + Dueling DQN, PER, target net, Huber loss, Adam lr **5e-4**, γ **0.99**, batch **64** | `agents/double_dqn_agent.py` |
| 9 | PER | α **0.6**, β **0.4 → 1.0** over 20,000 steps, ε 1e-6, sum-tree, buffer **50,000** | `agents/replay.py` |
| 10 | Target sync | hard copy every **500** learner steps → **53** syncs over the full run | `rl.target_update_every` |
| 11 | Training length | **112 episodes × 1200 s = 240 steps each = 26,880 control steps** | `outputs/logs/train.csv` |
| 12 | Training wall time | **33.4 seconds** total | sum of `seconds` column |
| 13 | Exploration | ε **1.0 → 0.05** linearly over 22,000 steps → floor reached at **episode 91** | `rl.epsilon_*` |
| 14 | Curriculum | low/medium/high/rush **cycled round-robin**, 28 episodes each — cycling, not ramping | `trainer.py:76` |
| 15 | Shipped model | greedy-eval **best**, tag `ep70_wait27.0`, ε counter **16,800 = 70×240**; final weights kept separately | `models/pretrained/atsc_2x2.pt` |
| 16 | Evaluation | **36 runs** = 3 controllers × 4 densities × 3 seeds (0,1,2), 1800 s, 30 s warm-up | `outputs/benchmark_results.csv` |
| 17 | Headline wait | RL vs fixed: **−15.1 % / −26.5 % / −48.8 % / −61.2 %** (low→rush), mean **≈38 %** | recomputed from CSV |
| 18 | Max-pressure | **19.22 / 22.52 / 35.56 / 50.54 s** — it beats RL at low and medium, loses at high and rush | same CSV |
| 19 | Throughput | **−0.1 / +0.0 / +2.8 / +7.6 %** — demand-bound, the deck's +35–45 % is **not** met | same CSV |
| 20 | Emergency clearance | 26.0→20.0 (1.30×), 28.7→18.3 (1.56×), 80.0→41.7 (1.92×), **113.7→32.7 (3.48×)** | same CSV |
| 21 | Consistency | seed spread at rush: fixed **30.02 s** vs RL **2.69 s** → ~**11× more consistent** | same CSV |
| 22 | Codebase | **47 Python files, 5,463 LOC**, 959 front-end lines, **zero front-end dependencies** | verified inventory |
| 23 | Tests | **144 tests, all passing**, in 13 files (26 in 6 files in 1.0.0) | `tests/` |
| 24 | Reproducibility | **36/36** benchmark rows reproduce the shipped CSV to **1e-9** | reran this week |
| 25 | Honest boundary | all numbers from the **built-in** backend; SUMO implemented but **not measured**; **QMIX disabled** | `backend: auto`, `qmix.enabled: false` |

Three derived figures worth being able to produce on demand, because they prove you
understand rather than memorised:

- **26,880** = 112 episodes × (1200 s ÷ 5 s). It is the ε step counter stored inside
  `atsc_2x2_final.pt`; the shipped best model `atsc_2x2.pt` stores **16,800 = 70 × 240**,
  matching its own `ep70` tag. That arithmetic is how you prove the checkpoints came from
  that exact logged run.
- **249** = the gap between each checkpoint's ε counter and its `learner_steps`
  (16,800 → 16,551 and 26,880 → 26,631). Four transitions arrive per control step, so a
  1,000-transition `min_buffer` blocks training for the first 249 steps. Identical in both
  files.
- **8 %** ≈ the capacity lost to clearance in the worst case: 5 s of yellow-plus-all-red per
  switch, at most one switch per 60 s max-green.
- **107,520** transitions = 4 agents × 26,880 steps, through a 50,000 buffer → it wrapped
  about 2.1×, so the final buffer holds roughly the last 52 episodes.

---

## 13. The two-minute opening

Say this, unprompted, at the start. It front-loads the honest framing so every later answer
is consistent with it. Timings are for spoken pace.

> **(0:00–0:20 — the problem)**
> Fixed-time traffic signals run a clock. They are tuned once for an assumed demand and they
> keep running that plan when demand changes, so green time gets spent on empty approaches
> while a queue builds next door. On our corridor the east–west arterial carries 2.2 times
> the side-street demand, and a symmetric fixed cycle simply cannot express that.
>
> **(0:20–0:50 — what I built)**
> I built an adaptive controller for a 2×2 grid of four signalised junctions. Each junction is
> its own reinforcement-learning agent — a Double Dueling DQN with prioritized experience
> replay. Every five seconds each agent reads 23 numbers: its own four queues and waiting
> times, its current phase, how long that green has been running, emergency flags, and its
> neighbours' pressures and phases. It outputs one of two choices: serve north–south, or serve
> east–west. The four agents share one set of weights, so they learn from four times the
> experience, but they decide independently.
>
> **(0:50–1:15 — safety, said before being asked)**
> The agent never controls the light directly. It *requests* a phase, and a finite-state
> machine decides — enforcing a 10-second minimum green, a 60-second maximum green, and a
> mandatory 3-second amber plus 2-second all-red on every change.
> Conflicting greens are not unlikely in this design; they are unreachable. That safety layer
> is hardcoded and unit-tested, because safety you can prove beats safety you hope was learned.
>
> **(1:15–1:45 — results, with the caveat attached)**
> I compared it against two baselines on identical random seeds: a standard fixed 60-second
> cycle, and max-pressure, which is the recognised adaptive research baseline. Across four
> traffic densities and three seeds — 36 runs — average waiting time falls 15 % in light
> traffic and 61 % at rush hour versus fixed-time. Emergency vehicles clear 3.5 times faster
> under saturation. The result I find most defensible is consistency: seed to seed, the RL
> controller varies by 2.7 seconds at rush where fixed-time varies by 30.
>
> **(1:45–2:00 — the boundary, volunteered)**
> Two things I want to state rather than be asked. Everything is in simulation, on synthetic
> Poisson demand, with a point-queue model that does not reproduce spill-back — so the
> direction of these results is solid but the magnitudes are simulator-specific. And
> max-pressure still beats my controller when the roads are quiet; it wins only where
> congestion makes adaptivity matter. I am happy to open any part of the code.

Why this works: it names the weakness twice before the panel does, which converts their
strongest line of attack into evidence that you understand your own system.

---

## 14. The twenty-five questions to rehearse first

Ranked by probability × damage. If time is short, rehearse only these.

| Rank | Q# | The question, in short |
|---|---|---|
| 1 | 144 | Summarise your project in thirty seconds |
| 2 | 118 | Did you use SUMO or not — be precise |
| 3 | 93 | Max-pressure beats you at low and medium. Explain |
| 4 | 112 | Your deck claims +35–45 % throughput; the data says +0.1–7.6 % |
| 5 | 86 | Isn't the emergency result just your override, not the RL? |
| 6 | 75 | How do you guarantee the light never shows conflicting greens? |
| 7 | 62 | Is it really multi-agent if the agents share one network? |
| 8 | 133 | Did you invent anything, or assemble known parts? |
| 9 | 105 | Your loss goes *up*. Is training broken? |
| 10 | 113 | A 108 % improvement in "average speed" cannot be real |
| 11 | 131 | Is this deep RL or just deep learning? |
| 12 | 120 | What does your simulator *not* model? |
| 13 | 132 | You optimise waiting time and report waiting time — circular? |
| 14 | 139 | What did *you* build versus what came from libraries? |
| 15 | 134 | Would this work on a real road tomorrow? |
| 16 | 67 | What is QMIX and why is it off? |
| 17 | 129 | What is *not* tested? |
| 18 | 110 | Only three seeds? |
| 19 | 100 | Training took 33 seconds — how? |
| 20 | 115 | What is your single strongest result? |
| 21 | 79 | What guarantees no road is starved? |
| 22 | 101 | What is the curriculum, exactly? |
| 23 | 140 | What was the hardest part? |
| 24 | 135 | Show me where your policy is worse than a baseline |
| 25 | 127 | Is the dashboard secure? |

A pattern worth noticing: **nine of the top twenty-five are invitations to overclaim.** The
answer that scores is the one that concedes the precise limit and then says what is still true.

---

## 15. What you may honestly claim — and what you may not

This section exists because the fastest way to fail a viva is to claim one thing you cannot
open and explain. Use the columns literally.

**You wrote these. Claim them without hedging.**
The point-queue simulator (`sim/mini_backend.py`), the environment and its reward
(`envs/traffic_env.py`), the safety finite-state machine (`envs/phases.py`), the observation
builder (`envs/spaces.py`), the multi-agent wrapper (`agents/multi_agent.py`), the prioritized
replay buffer including its sum-tree (`agents/replay.py`), the NumPy Q-network **including its
backward pass** (`agents/net.py`), all three controllers and the preemption override
(`control/`), the training loop (`train/`), the evaluation and plotting harness (`eval/`), the
SUMO backend and network generator (`sim/sumo_backend.py`, `sim/netgen.py`), the entire
dashboard front end drawn on canvas with no libraries, the 26 tests, and the seeding discipline
that makes 36/36 rows reproduce exactly.

**These are published work you implemented. Say "I implemented", not "I designed".**
Double DQN (van Hasselt et al., 2016), Dueling architecture (Wang et al., 2016), Prioritized
Experience Replay (Schaul et al., 2016), DQN itself (Mnih et al., 2015), max-pressure control
(Varaiya, 2013), and Opticom-style rule-based preemption. Naming the origin *raises* your
credibility — it shows you know the literature you are standing on.

**Do not claim these. They are not in the project.**

| Do not say | The truth, if asked |
|---|---|
| "I used SUMO for my results" | SUMO is implemented and selectable; **every reported number is from the built-in backend** |
| "I used QMIX / CTDE" | Implemented, `qmix.enabled: false`, needs PyTorch, **no result used it** |
| "I trained with PyTorch" | The shipped model was trained by the from-scratch NumPy network; the checkpoint is portable to PyTorch |
| "I used computer vision / YOLO for detection" | **No such code exists in this project.** Queues come from the simulator |
| "I validated on real traffic data" | Demand is synthetic Poisson; **no real dataset is used anywhere** |
| "I beat SCATS / SCOOT" | Not compared — no reference implementation was run |
| "I ran ablations on PER and Double DQN" | **Not run.** They are listed as the first two next experiments |
| "35–45 % throughput improvement" | Measured **+0.1 % to +7.6 %**; the target was not met and the docs now say so |
| "It is ready for deployment" | Simulation only, oracle sensing, unauthenticated dashboard, no spill-back model |
| "Ponytail is part of the system" | It is a development-time assistant plugin that shaped code style. Correct phrasing: *"I used Ponytail to keep the codebase lean."* It is **not** a runtime component |

**The three sentences that will earn you the most credit**, because each one gives away
something an examiner expected to have to extract:

1. "Max-pressure beats my controller at low and medium density — it wins only where
   congestion makes adaptivity matter, and I report both."
2. "The throughput target in my proposal was not met; throughput is bounded by demand, and I
   left the number in rather than quietly dropping the metric."
3. "Everything here is simulation. The direction of the result is solid; the magnitude is
   specific to a point-queue model that cannot produce spill-back."

---

## 16. The hardware model — the questions it invites (1.2.0)

What it is, in one breath: an Arduino Uno that shows the RL grid's lamps on 16 physical
signal heads in real time, adds toy cars seen by 8 IR sensors at J0_0 to the simulation, and
takes emergency and pause requests from a 433 MHz remote. Build and demo: `docs/HARDWARE.md`.

| # | Question | Answer | If they push |
|---|---|---|---|
| H1 | Does the Arduino run the RL model? | No. The PC runs the simulation and the four agents exactly as on the dashboard; the Arduino is the controller cabinet's *output and input stage* — lamps, detectors, a preemption receiver. | Running a 23-input, two-layer 128-unit network on an ATmega328P (2 KB RAM) is not realistic, and it would split the model from the simulation it is being judged in. A real deployment would put the policy on the controller's own CPU. |
| H2 | How do 48 LEDs fit on an Uno? | Six 74HC595 serial-in, parallel-out shift registers in a chain: 3 pins (data, clock, latch) drive 48 outputs. The latch makes every change appear at once, so no in-between state is ever lit. | At most one LED per head is lit, so at most three per chip — well inside the 74HC595's current limits. |
| H3 | Why does the board show the lamps a moment later than the screen? | The simulation decides 5 s at a time and runs each decision in milliseconds. The PC queues each simulated second's lamp state and plays them out one per real second, so a 3 s amber lasts 3 s. The board trails the newest decision by up to one interval, as the browser does. | `src/atsc/hw/mirror.py`; tested with a fake clock (amber exactly 3.0 s, all-red 2.0 s) and on the simulated board. |
| H4 | What happens if the PC fails? | If the board hears nothing for 3 s — program closed or crashed, data link lost — every head flashes amber, the standard failsafe of a signal whose controller has failed. The dashboard reconnects by itself (it retries the port every 2 s), so a cable pulled and plugged back is live again in about 3 s. | The board keeps a link watchdog (`LinkWatch` in `atsc_core.h`); the PC resends the full state at least once a second, so a single lost line is repaired by the next. |
| H5 | Could electrical noise switch a light? | Every line carries a CRC-8 (the ATM polynomial 0x07); a damaged line is dropped, and a `>` always starts a new frame, so the reader resynchronises by itself. | Tested on the PC against the same vectors as the Python side, and with 120-byte bursts on the simulated board. |
| H6 | Doesn't a sensed toy car make the race unfair or change your results? | It is added to *both* grids at the same moment, so the comparison stays like-for-like. It is added without drawing a random number, so the simulated arrivals around it are exactly what the seed produces anyway. Training and the benchmark never call this code. | `tests/test_hardware.py::test_detected_vehicles_never_shift_the_simulated_traffic` checks the random generators' states are identical with and without sensed cars. |
| H7 | What do the two sensors per approach do? | The *arrival* sensor counts each car that passes (one simulated vehicle each). The *queue* sensor at the stop line, when blocked for 0.4 s, means "a queue has formed": the PC tops that simulated queue up to 3 vehicles, and repeats every 5 s while it stays blocked, like a presence loop. | In our runs a car parked on the north queue sensor got green after a median of 15 s at medium demand — the 10 s minimum green plus 5 s clearance is the earliest the FSM allows. |
| H8 | Why ASCII and not a binary protocol? | You can read it in the Arduino Serial Monitor, which makes the board debuggable by eye; at 115200 baud the 15-byte lamp frame takes about 1.3 ms. | Bandwidth is not the constraint: a frame on each change plus one per second is a few hundred bytes a second. |
| H9 | Why a wired USB link, not Wi-Fi or Bluetooth? | Determinism and zero configuration in an exam room: no network to join, no pairing, the board is powered by the same cable. The protocol is transport-independent — an ESP32 over Wi-Fi would only replace `SerialLink`. | The 433 MHz remote shows the wireless part where it matters: preemption from a vehicle. |
| H10 | How was the firmware tested without hardware? | Its logic (`atsc_core.h`) compiles on the PC and is tested against the Python protocol. The full firmware ran unchanged on a simulated ATmega328P (simavr), wired to a simulated 74HC595 chain, OLED, sensors and remote, connected to the real dashboard: every lamp change matched the RL grid. | What a simulator cannot test is the soldering, the sensors' sensitivity and the remote's pairing — that is what `python run.py hwtest` is for. |
| H11 | How far is this from a real signal controller? | Far, and say so: no conflict monitor (an independent circuit that cuts power if conflicting greens are ever lit), no mains lamps, no NTCIP/UTMC communication, and the safety logic runs on the PC, not on the cabinet. What it does share with real practice: the controller enforces amber and all-red, the detectors are presence/count sensors, preemption is a request, and loss of communication falls back to flashing amber. | §10 of this file lists what real deployment would need. |

---

*End of VIVA_MASTER.md. Verification state at time of writing: 26/26 tests pass,
`python run.py doctor` reports config and checkpoint OK, and all 36 benchmark rows reproduce
the shipped CSV to within 1e-9.*




















