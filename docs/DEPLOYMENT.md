# Deploying the live dashboard

The dashboard is a single long-lived Python process: FastAPI serves four endpoints and an
`asyncio` background task pushes a JSON snapshot to every connected browser every 200 ms.
That shape decides everything below — it rules out static hosting and serverless
functions, and it rules in a small always-on container.

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | the dashboard page (`src/atsc/dashboard/static/index.html`) |
| `/static/*` | GET | `app.js` and `styles.css`, same origin, no CDN |
| `/api/state` | GET | one snapshot — also the health-check path |
| `/api/cmd` | POST | play / pause / step / reset / speed / scenario / emergency |
| `/ws` | WS | the live stream, and the same commands in the other direction |

---

## Why Render

| Platform | WebSockets | Free tier | GitHub push -> auto-deploy | Verdict |
|---|---|---|---|---|
| **Render** | yes | yes, no card | **native, built in** | **chosen** |
| Hugging Face Spaces | yes | yes, 2 vCPU / 16 GB | only via a mirror workflow + HF token | best fallback |
| Fly.io | yes | small allowance, card required | via GitHub Actions | fine, needs a card |
| Railway | yes | trial credit only, then paid | native | not a durable free tier |
| Vercel / Netlify / GitHub Pages | no long-lived process | — | native | **cannot run this app** |

GitHub Pages was ruled out first: it serves static files and cannot execute Python.
Vercel and Netlify functions are ruled out for a subtler reason — a serverless invocation
cannot hold a WebSocket open or keep the broadcaster task alive between requests, so the
race would freeze the moment the function returned.

Render is the only option that is free without a card **and** redeploys straight from a
`git push`, which is exactly what was asked for. [`render.yaml`](../render.yaml) in the
repository root means the whole service is defined in code: no settings to fill in, and
the configuration is reviewable in the diff.

The one real cost of the free plan is that the instance **spins down after 15 minutes of
inactivity**, so the first visit after a quiet period takes roughly 50 seconds to wake.
If a live demo is being graded, open the page a minute before you present it.

---

## Deploy on Render

1. Push this repository to GitHub (the branch must be `main`).
2. Sign in at <https://dashboard.render.com> with the GitHub account that owns the repo.
3. **New +** -> **Blueprint**, choose the repository, and let it read `render.yaml`.
4. Confirm the plan shows **Free**, then **Apply**. The first build takes 2-4 minutes:
   it installs four wheels, so there is nothing to compile.
5. Watch the log for `Uvicorn running on http://0.0.0.0:10000` followed by
   `ASGI app ready (scenario=medium, backend=numpy, checkpoint=True)`.
6. Open the URL Render prints. **If it is not
   `https://adaptive-traffic-signal-rl.onrender.com`** — the name may already be taken —
   copy the real one and update the two links at the top of `README.md`.

Nothing else is required. There are no secrets, no database and no API keys to add.

### If the build fails on numpy

Render's default native Python is now 3.13 and `numpy==1.26.4` publishes no 3.13 wheel,
so an unpinned build tries to compile numpy from source and runs out of memory. That is
why `render.yaml` sets `PYTHON_VERSION: "3.12.6"`. If you deploy without the Blueprint,
set that environment variable by hand.

### If the region is rejected

`render.yaml` asks for `singapore`, the closest region to India. Delete the `region:`
line to fall back to Render's default and re-sync.

---

## Alternative: Hugging Face Spaces

Worth the extra step if the demo must be instant: Spaces gives 2 vCPU and 16 GB of RAM
free, and only sleeps after about two days of inactivity, so there is no cold start
during a viva. The trade-off is that Spaces pulls from its own git remote, so a push to
GitHub does not redeploy it by itself.

1. Create a Space at <https://huggingface.co/new-space> -> SDK **Docker** -> **Blank**.
2. Add this front-matter at the top of the Space's own `README.md` (do not add it to the
   GitHub one — it would render as a stray table):

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

4. Spaces builds [`Dockerfile`](../Dockerfile) automatically. It copies only `asgi.py`,
   `config.yaml`, `src/` and the pretrained checkpoint — 51 files, 1.1 MB — and listens
   on `${PORT:-7860}` as a non-root user.
5. To make GitHub pushes propagate, add a workflow that mirrors `main` to the Space using
   an `HF_TOKEN` repository secret. Store the token in **GitHub -> Settings -> Secrets**,
   never in a file.

## Fly.io, Railway, or your own machine

The same `Dockerfile` covers all three; the port comes from `$PORT`.

```bash
docker build -t atsc .
docker run --rm -p 7860:7860 atsc      # http://localhost:7860
```

On Nixpacks-based platforms (Railway), point the install step at
`requirements-deploy.txt` explicitly — the default detection would install
`requirements.txt`, and with it torch, which the live site does not use.

---

## What was added, and why each file exists

| File | Role |
|---|---|
| [`asgi.py`](../asgi.py) | the entrypoint. `build_app()` is a *factory* — it needs a live session before it can register a route — so the package has no module-level `app` and `uvicorn atsc.dashboard.server:app` cannot work. This module puts `src/` on `sys.path` exactly as `run.py` does, builds the session, presses play, and exposes `app`. It never calls `uvicorn.run()` and never opens a browser. |
| [`requirements-deploy.txt`](../requirements-deploy.txt) | four pins: numpy, PyYAML, fastapi, uvicorn[standard]. `requirements.txt` is untouched and stays the local development set. |
| [`render.yaml`](../render.yaml) | the Render Blueprint: free plan, `autoDeploy` from `main`, build and start commands, health check, pinned Python. |
| [`Dockerfile`](../Dockerfile) | Spaces / Fly / Railway / local container. Non-root, `${PORT:-7860}`. |
| [`.dockerignore`](../.dockerignore) | keeps the build context to what the server reads. |
| [`Procfile`](../Procfile) | one line, for platforms that look for it. |
| `.gitignore` | gained `.env`, `.env.*`, `*.pem`, `*.key`, `secrets.*`, `credentials.json`, `*_token.txt`, `.netrc`. |

Not one line of the simulator, the agents, the safety FSM, the reward function or the
dashboard's JavaScript was changed. The deployed page is the local page.

### Why no torch on the server

`rl.nn_backend: auto` falls back to the from-scratch NumPy dueling network in
`src/atsc/agents/net.py`, which loads the same shipped checkpoint and picks the same
greedy actions. Walking `sys.modules` after a full session boot, a scenario change and an
emergency injection shows the dashboard path importing only **numpy** and **yaml** from
the project side. Peak RSS with both simulations running is **41 MiB**, and one broadcast
tick costs **0.8 ms** at 1x speed and **4.7 ms** at 8x against a 200 ms budget — about 2 %
of one core at the worst setting. That is what makes a 512 MB free instance comfortable.

## Environment variables

None are required. All four are optional.

| Variable | Set by | Meaning |
|---|---|---|
| `PORT` | the platform | which port to bind. Render injects it; the Dockerfile defaults to 7860. |
| `PYTHON_VERSION` | `render.yaml` | pinned to `3.12.6` so numpy resolves to a wheel. |
| `ATSC_CONFIG` | you, optionally | path to an alternative `config.yaml`. |
| `ATSC_SCENARIO` | you, optionally | `low`, `medium`, `high` or `rush` as the opening demand. |

There is no secret to configure. If you ever add one, put it in the platform's own
environment-variable panel — never in a committed file.

---

## Check the deployment

Open the site, then open the browser console (F12) and the Network tab.

| # | Check | What you should see |
|---|---|---|
| 1 | the site loads | dark dashboard, no framework error page |
| 2 | the dashboard renders | two network panels side by side, KPI cards above them |
| 3 | all four approaches draw | N, E, S and W queues at each of the eight junctions |
| 4 | signals work | phase badges flip `NS` <-> `EW`, with amber and all-red between |
| 5 | vehicles appear | cars, bikes, autos, e-rickshaws, vans, buses, trucks |
| 6 | vehicles move | queues drain on green and grow on red |
| 7 | the RL policy runs | header reports the checkpoint loaded; left panel keeps shorter queues |
| 8 | the fixed-time policy runs | right panel switches on a fixed 30 s split regardless of demand |
| 9 | the WebSocket connects | Network -> WS shows `/ws` with status **101** |
| 10 | updates are live | frames arrive about five times a second; the charts advance |
| 11 | no console errors | console is empty |
| 12 | no failed requests | every request is 200, or 101 for the socket |
| 13 | no localhost references | search the page source: no `127.0.0.1`, no `localhost` |
| 14 | static assets load | `/static/app.js` and `/static/styles.css` both 200 |
| 15 | no CORS errors | everything is same-origin, so there should be none |
| 16 | no mixed content | the socket URL starts `wss://`, not `ws://` |

Checks 13, 15 and 16 hold by construction rather than by luck: `app.js` derives both
URLs from the browser's own origin —

```javascript
const proto = location.protocol === "https:" ? "wss" : "ws";
ws = new WebSocket(`${proto}://${location.host}/ws`);
```

— and every other request is a relative path (`fetch("/api/state")`,
`fetch("/api/cmd")`, `/static/...`). There is no CDN and no hardcoded host anywhere in
the frontend, so the same file works on `127.0.0.1:8000` over HTTP and on a hosted
HTTPS domain with no edit.

Before pushing, the same behaviour can be checked without a browser:

```bash
python run.py demo        # then browse to http://127.0.0.1:8000
```

---

## Living with the free tier

**Cold start.** The instance sleeps after 15 minutes with no traffic and takes roughly
50 seconds to answer the next request. Open the tab before you need it.

**One simulation, shared by everyone.** A single `DashboardSession` serves all visitors,
by design: two people opening the page see the same race, frame for frame, and either of
them can press pause or inject an ambulance. For a public demo that is the point. It also
means `/api/cmd` is deliberately unauthenticated — there is no account, no upload, no
database and no filesystem write behind it, so the worst a stranger can do is pause your
traffic or switch the scenario. If you ever need a private demo, deploy a second instance
rather than adding auth to the graded code path.

**It keeps simulating with nobody watching.** The broadcaster advances the session every
tick whether or not anyone is connected. At about 2 % of a core that is affordable, and it
means a visitor arrives mid-race instead of at a standing start.

**No persistence.** Nothing is written to disk. A redeploy or a wake-from-sleep starts a
fresh episode from `seed: 42`, which is also why the demo is reproducible.

## Redeploying after a push

`autoDeploy: true` means Render rebuilds on every push to `main`:

```bash
git add -A
git commit -m "your change"
git push origin main
```

The build log appears in the Render dashboard within a few seconds. Editing
`render.yaml` itself also triggers a re-sync of the service definition. To pause
automatic builds, set `autoDeploy: false` and push, or use **Suspend** in the Render UI.

## Local development is unchanged

Nothing above alters how the project runs on your machine.

```bash
python run.py demo           # dashboard on http://127.0.0.1:8000
python run.py doctor         # dependency and environment check
python run.py train --quick  # 8 episodes
python run.py eval           # the 36-run benchmark
pytest -q                    # 26 unit tests
```

`requirements.txt` still installs the full development set including torch, pandas,
matplotlib and pytest. `config.yaml` remains the single source of truth: the deployment
reads the same file, and `dashboard.host: 127.0.0.1` stays correct locally because on a
host the bind address comes from the start command instead.




