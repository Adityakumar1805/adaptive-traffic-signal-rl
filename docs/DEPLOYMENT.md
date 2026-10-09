# Deploying the live dashboard

The dashboard is a single long-lived Python process. FastAPI serves the page, a small JSON
API and one WebSocket; an `asyncio` background task advances the shared race every 200 ms
and pushes one compact frame to every connected browser. That shape decides everything
below — it rules out static hosting and serverless functions, and it rules in a small
always-on container.

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | the page (`static/index.html`, build id and transport filled in, strict CSP) |
| `/static/*` | GET | `styles.css`, `icon.svg`, `js/*.js` — same origin, no CDN, ETag-revalidated |
| `/sw.js` | GET | the service worker (served from the root so it can cache `/`) |
| `/ws` | WS | `hello` + frames out; commands and pings in |
| `/api/hello` | GET | the same `hello` (polling fallback) |
| `/api/state` | GET | a keyframe of the current state (polling fallback) |
| `/api/cmd` | POST | play / pause / step / reset / speed / scenario / inject — 400 with a reason if invalid |
| `/healthz` | GET | liveness + version, build, viewers, tick cost, payload size, CPU, memory — the health-check path |

---

## Why Render

| Platform | WebSockets | Free tier | GitHub push -> auto-deploy | Verdict |
|---|---|---|---|---|
| **Render** | yes | yes, no card | **native, built in** | **chosen** |
| Hugging Face Spaces | yes | yes, 2 vCPU / 16 GB | only via a mirror workflow + HF token | best fallback |
| Fly.io | yes | small allowance, card required | via GitHub Actions | fine, needs a card |
| Railway | yes | trial credit only, then paid | native | not a durable free tier |
| Vercel / Netlify / GitHub Pages | no long-lived process | — | native | **cannot run this app** |

GitHub Pages serves static files and cannot execute Python. Vercel and Netlify functions
cannot hold a WebSocket open or keep the broadcaster task alive between requests, so the
race would freeze the moment the function returned. Render is free without a card **and**
redeploys straight from a `git push`; [`render.yaml`](../render.yaml) defines the whole
service in code.

The real cost of the free plan: the instance **spins down after 15 minutes without
traffic**, and the next visit waits for it to boot (typically under a minute). Returning
visitors see the page's own **Waking up the server…** screen during that time (see
[Living with the free tier](#living-with-the-free-tier)).

---

## Deploy on Render

1. Push this repository to GitHub (the branch must be `main`).
2. Sign in at <https://dashboard.render.com> with the GitHub account that owns the repo.
3. **New +** -> **Blueprint**, choose the repository, and let it read `render.yaml`.
4. Confirm the plan shows **Free**, then **Apply**. The first build takes 2-4 minutes: it
   installs 38 pinned wheels, so nothing is compiled.
5. Watch the log for these lines:

   ```
   ASGI app ready (scenario=medium, backend=numpy, checkpoint=atsc_2x2.pt, build=<12 hex>) - one shared session serves every visitor
   Uvicorn running on http://0.0.0.0:10000
   ```

   and, once you open the page, `"WebSocket /ws" [accepted]` and `connection open`.
6. Open the URL Render prints. **If it is not
   `https://adaptive-traffic-signal-rl.onrender.com`** — the name may already be taken —
   update the links at the top of `README.md`.

There are no secrets, no database and no API keys to add.

An existing service created before this release keeps working: Render re-reads
`render.yaml` on every push to `main`, so the new start command (`--ws-max-size 65536
--ws-ping-interval 20 --ws-ping-timeout 20`) and health-check path (`/healthz`) are picked up
automatically. If the service was created by hand rather than as a Blueprint, copy those two
settings into **Settings** yourself.

### If the build fails on numpy

Render's default Python is newer than `numpy==1.26.4` supports, so an unpinned build tries
to compile numpy from source and fails. That is why `render.yaml` sets
`PYTHON_VERSION: "3.12.6"`. If you deploy without the Blueprint, set that environment
variable by hand.

### If the region is rejected

`render.yaml` asks for `singapore`, the closest region to India. Delete the `region:` line
to fall back to Render's default and re-sync.

---

## Why the live site used to say "WebSocket connection failed"

Every `/ws` handshake was answered with **HTTP 403**, so the page silently fell back to
polling full state four times a second. The cause was one import. `server.py` uses
`from __future__ import annotations`, which turns every annotation into a string that
FastAPI resolves against the module's globals; `WebSocket` was imported only inside
`build_app()`, so FastAPI could not resolve `sock: "WebSocket"`, treated the socket as a
required *query parameter*, and rejected every connection that did not carry `?sock=...`.

The fix imports `WebSocket` at module level (inside `try/except ImportError`, so the
zero-dependency fallback still works without FastAPI), and
`tests/test_dashboard_ws.py::test_websocket_route_takes_no_query_parameters` fails if it ever
comes back. Two further tests open real sockets: one through FastAPI's test client, one
through a real uvicorn server with the `websockets` library — the production stack.

---

## Alternative: Hugging Face Spaces

Worth it if the demo must be instant: Spaces gives 2 vCPU and 16 GB of RAM free and only
sleeps after about two days of inactivity. Spaces pulls from its own git remote, so a push
to GitHub does not redeploy it by itself.

1. Create a Space at <https://huggingface.co/new-space> -> SDK **Docker** -> **Blank**.
2. Add this front-matter at the top of the Space's own `README.md` (not the GitHub one):

   ```yaml
   ---
   title: Adaptive Traffic Signal Control
   sdk: docker
   app_port: 7860
   ---
   ```

3. Push this repository to the Space remote:

   ```bash
   git remote add space https://huggingface.co/spaces/<user>/<space-name>
   git push space main
   ```

4. Spaces builds [`Dockerfile`](../Dockerfile): `python:3.12.6-slim`, the pinned
   `requirements-deploy.txt` (binary wheels only), `asgi.py`, `config.yaml`, `src/` and the
   shipped checkpoint; a non-root user; `${PORT:-7860}`; a `HEALTHCHECK` on `/healthz`.
5. To make GitHub pushes propagate, add a workflow that mirrors `main` to the Space using an
   `HF_TOKEN` repository secret (GitHub -> Settings -> Secrets — never in a file).

## Fly.io, Railway, or your own machine

The same `Dockerfile` covers all three; the port comes from `$PORT`.

```bash
docker build -t atsc .
docker run --rm -p 7860:7860 atsc      # http://localhost:7860
```

On Nixpacks-based platforms (Railway), point the install step at `requirements-deploy.txt`
explicitly — the default detection would install `requirements.txt` and with it torch, which
the live site does not use. Always run **one** worker: every visitor shares one race.

---

## Files

| File | Role |
|---|---|
| [`asgi.py`](../asgi.py) | the entrypoint. `build_app()` is a factory that needs a live session, so the package has no module-level `app`. This module builds the session, presses play and exposes `app`; it never calls `uvicorn.run()` and never opens a browser. `ATSC_SCENARIO` with a typo is logged and ignored instead of crashing the site. |
| [`requirements-deploy.txt`](../requirements-deploy.txt) | every runtime package pinned, transitive ones included (38). `websockets` is held at 13.1, the last release before its legacy server API — the one uvicorn 0.30.1 uses — was deprecated. |
| [`render.yaml`](../render.yaml) | the Render Blueprint: free plan, `autoDeploy` from `main`, build and start commands, `/healthz` health check, Python 3.12.6. |
| [`Dockerfile`](../Dockerfile) / [`.dockerignore`](../.dockerignore) | Spaces / Fly / Railway / local container; the ignore file keeps the build context to what the server reads (and keeps the dashboard's own `.svg`). |
| [`Procfile`](../Procfile) | one line, the same start command, for platforms that look for it. |
| [`tools/e2e_browser.py`](../tools/e2e_browser.py) | headless-browser check of a running site (local or deployed). |
| [`tools/bench_server.py`](../tools/bench_server.py) | per-tick cost and payload in-process, or a load test with N WebSocket viewers. |

### Why no torch on the server

`rl.nn_backend: auto` falls back to the from-scratch NumPy dueling network in
`src/atsc/agents/net.py`, which loads the same shipped checkpoint and picks the same greedy
actions. The dashboard path imports only numpy and PyYAML from the project side.

Measured on the deploy stack (Python 3.12.6, `pip install -r requirements-deploy.txt`, the
exact start command from `render.yaml`, one uvicorn worker, a 2.8 GHz Xeon; CPU is the
process's total as a percentage of one core):

| Load | Resident memory | CPU | Tick (simulate + encode) | Per viewer |
|---|---|---|---|---|
| idle, medium 1× | 76 MiB | 1.5 % | ~2 ms mean | — |
| 50 viewers, rush 1× | 88 MiB | 10 % | 3.2 ms mean, 5.8 ms p95 | 5.4 KB/s, 5 frames/s |
| 100 viewers, medium 1× (default) | 101 MiB | 17 % | 2.9 ms mean, 5.0 ms p95 | 4.0 KB/s, 5 frames/s |
| 60 viewers, rush 8× (worst case) | 91 MiB | 34 % | 16.1 ms mean, 22.7 ms p95 | 19.8 KB/s, 5 frames/s |

against a 200 ms tick, a 512 MiB instance and Render's **0.1 CPU** (10 % of a core) on the
free plan. Memory is never the limit; CPU can be. The default view leaves plenty of room
(1.5 % idle, about 0.15 % per extra viewer on this machine). Rush hour at 8× costs about
7 % with nobody watching, so on the free plan several viewers at 8× will tick slower than
real time — the page keeps interpolating smoothly between whatever frames arrive, and the
race simply advances more slowly. The 101st viewer is told
`{"t": "busy", "retry_s": 30}` (`dashboard.max_clients`) and retries later.

### One BLAS thread

The server pins NumPy's BLAS (OpenBLAS) to a single thread before NumPy loads
(`asgi.py` → `src/atsc/threads.py`). With the default pool, OpenBLAS's worker threads
spin-wait after every call, and the network is evaluated five times a second, so they never
sleep: version 1.0.0 used **about 47 % of a core with no viewers at all** — almost five times the
free plan's whole allowance, so the process was throttled continuously. `/healthz` shows
the result as `cpu_percent`: a few percent while idle is healthy; a value stuck at about
10 on the free plan means the instance is saturated. To override (say, on a bigger
instance), set `OPENBLAS_NUM_THREADS` yourself — an explicit value is respected.

## Environment variables

None are required.

| Variable | Set by | Meaning |
|---|---|---|
| `PORT` | the platform | which port to bind. Render injects it; the Dockerfile defaults to 7860. |
| `PYTHON_VERSION` | `render.yaml` | pinned to `3.12.6` so every package resolves to a wheel. |
| `ATSC_CONFIG` | you, optionally | path to an alternative `config.yaml`. |
| `ATSC_SCENARIO` | you, optionally | `low`, `medium`, `high` or `rush` as the opening demand. |
| `OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS`, `MKL_NUM_THREADS` | `asgi.py` (default `1`) | math-library threads; see "One BLAS thread" above. Set one only to override. |

If you ever add a secret, put it in the platform's own environment-variable panel — never
in a committed file.

---

## Post-deploy checklist

Open the site on a desktop browser with DevTools (F12) open, then on a phone.

| # | Check | What you should see |
|---|---|---|
| 1 | the build is the new one | `https://<site>/healthz` returns JSON with `"version": "1.2.0"`, a `build` id, `"tick_errors": 0`, and `cpu_percent.last_30s` in low single digits while nobody else is watching (a value near 10 means the 0.1-CPU instance is saturated) |
| 2 | the WebSocket connects | the bottom bar reads **live · streaming** with a green dot; Network -> WS shows `/ws` with status **101** |
| 3 | no console errors | the Console is empty, and the Issues tab has no "form field" or CSP issues |
| 4 | no failed requests | every request is 200 / 304, or 101 for the socket; no 403 on `/ws` |
| 5 | both grids fully visible | laptop / desktop: the two 2×2 grids side by side with every junction and stub visible **without scrolling**, the KPI cards in a column on the right; phone: controls, then the whole RL grid on the first screen; no sideways scrolling anywhere |
| 6 | signals | each approach has a lamp head and a coloured stop line; amber and all-red appear between greens (easiest at 0.2× speed) |
| 7 | every vehicle type appears | the **Vehicles** legend lists 18 types; within a few minutes at Rush hour 8× every regular type has shown a non-zero count at least once |
| 8 | every Inject button works | press **Ambulance**, **Police car**, **Fire engine** in turn: the vehicle appears on both grids with flashing lights, the red-blue banner (top of the right-hand column on a laptop) names it and the pre-empted junction, a pulsing ring marks that junction, the banner switches to "through the RL grid — still in fixed-time traffic" if RL clears it first, and the emergency clearance card shows RL's time once the vehicle has left the RL grid, and "N× faster" once it has left both |
| 9 | controls | Pause / Step / Reset / Speed / Scenario change the race (they are shared: every visitor sees the change) |
| 10 | KPI signs | waiting time and queue show RL's change against fixed-time, e.g. **−27 %** in green, the same convention as the README table |
| 11 | reconnect | toggle the network off and on (DevTools -> Network -> Offline): the bar shows **reconnecting…**, then **live · streaming** again without a reload |
| 12 | wake screen | after the service has slept (15 min idle), reopen the page: **Waking up the server…** appears, then the dashboard connects by itself |
| 13 | no mixed content | the socket URL starts with `wss://` on the HTTPS site |

`python tools/e2e_browser.py https://<site>` automates checks 1-10 in headless Chromium
(desktop and phone sizes; it prints the version and build it found) and saves screenshots;
add `--no-controls` if other people are watching, because it briefly switches the shared
race to rush hour at 8×.

### If the bar does not say "live · streaming"

| The bar says | Meaning | What to do |
|---|---|---|
| **live · polling** | HTTP works but the socket does not | Network -> WS: a **403** means the old `server.py` is still deployed (check `/healthz` for version 1.2.0); a failed upgrade with no response usually means a proxy, VPN or antivirus blocks WebSockets on your network — try another network or a phone on mobile data |
| **connecting…** / **reconnecting…** for long | the server is not answering | Render dashboard -> the service -> **Logs**: look for a crash or a failed health check; **Events** shows whether the deploy finished |
| **Waking up the server…** for more than ~2 min | the free instance is not starting | check the Render dashboard for a failed deploy or a suspended service |
| **server full · retrying** | `dashboard.max_clients` viewers are connected | raise it in `config.yaml` if the instance has room (see the measurements above) |
| **live · streaming**, but the race crawls or stutters | the instance is short of CPU | `/healthz`: `cpu_percent.last_30s` near 10 and a high `tick_ms.p95` mean the free plan's 0.1 CPU is used up — usually someone left it at Rush hour 8×; set the speed back to 1× |

The Logs and Events checks need your Render dashboard; everything else can be checked from any browser.

---

## Living with the free tier

**Cold start.** The instance sleeps after 15 minutes with no traffic and boots on the next
request. A visitor who has opened the site before gets the cached page from the service
worker after 4 s and sees **Waking up the server…** with a timer; the page retries with
back-off and connects by itself. A first-ever visit cannot be served before the instance is
up (there is no page to show yet), so it waits on Render's own loading behaviour. Open the
tab a minute before you present.

**One simulation, shared by everyone.** A single `DashboardSession` serves all visitors, by
design: two people opening the page see the same race, frame for frame, and either can press
pause or dispatch an ambulance. Commands are validated (an unknown scenario is refused with
a message instead of breaking the race), rate-limited per viewer, and capped
(`dashboard.max_active_emergencies`). `/api/cmd` is unauthenticated on purpose — there is no
account, upload, database or filesystem write behind it.

**Hidden tabs disconnect.** A tab hidden for a minute closes its socket and reconnects when
it is shown again, so forgotten background tabs do not use bandwidth or keep a slot.

**No persistence.** Nothing is written to disk. A redeploy or a wake-up starts a fresh
episode from `seed: 42`.

**Bandwidth.** About 3.9 KB/s per viewer at the default speed (20 KB/s at rush 8×), i.e.
roughly 14 MB per viewer-hour. Free web services draw on the workspace's included outbound
bandwidth, which Render's pricing page lists as 5 GB a month on the Hobby plan (checked
October 2026) — about 350 viewer-hours at the default speed. The Billing page of the Render
dashboard shows what has been used.

## Redeploying after a push

`autoDeploy: true` means Render rebuilds on every push to `main`:

```bash
git add -A
git commit -m "your change"
git push origin main
```

The build log appears in the Render dashboard within a few seconds. When the new version is
live, `/healthz` reports the new `build` id, and a tab that was open on the old version
reloads itself once when it reconnects (the page compares its build id with the server's).

The same push runs GitHub Actions ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)):
the test suite, the 36-run benchmark reproduced byte for byte from the shipped checkpoint
(with PyTorch and with the NumPy network), and this deploy stack — `requirements-deploy.txt`
on Python 3.12.6, `render.yaml`'s start command, `/healthz` and the WebSocket. Render does not
wait for it by default. To deploy only commits that pass, set **Settings → Auto-Deploy → After
CI Checks Pass** in the Render dashboard, or `autoDeployTrigger: checksPass` in `render.yaml`
(Render now documents `autoDeployTrigger: commit` as the replacement for the deprecated
`autoDeploy: true` that `render.yaml` uses; the two behave the same). Render then skips a commit whose
checks fail — and also one where no check ran at all.

## Local development

```bash
python run.py demo           # dashboard on http://127.0.0.1:8000
python run.py doctor         # dependency and environment check
python run.py train --quick  # 8 episodes, a separate *_quick.pt
python run.py eval           # the 36-run benchmark (built-in simulator)
pytest                       # 196 tests
python tools/e2e_browser.py --local   # the browser check above, against a local server
```

`requirements.txt` is the full development set (torch, pandas, matplotlib, pytest).
`config.yaml` is the single source of truth for both; `dashboard.host: 127.0.0.1` is right
locally because on a host the bind address comes from the start command.
