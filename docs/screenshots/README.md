# Screenshots

The stills and the animation in the top-level README. The benchmark plots are not copied
here: the README shows them straight from `outputs/`, where `python run.py eval` writes them.

| File | Shows |
|---|---|
| `dashboard_demo.gif` | the animation at the top of the README: rush hour at 0.5×, one ambulance dispatched into both grids; it crosses the RL grid in 43 simulated seconds and the fixed-time grid in 138, and the clearance card ends on "3.2× faster". Recorded with `python tools/record_gif.py` (headless Chromium + ffmpeg) |
| `dashboard_desktop.png` | the first screen on a 1440 x 900 display: both grids complete, the KPI cards in the column beside them |
| `dashboard_phone.png` | the first screen on a 390 x 844 phone, with an ambulance on the road |
| `network_render_ambulance.png` | one ambulance dispatched to both grids: on the RL grid it is already clearing J1_0; on the fixed-time grid J1_0 is pre-empted (the pulsing ring) while the ambulance is still in the queue counted by "+12"; the banner heads the KPI column |
| `dashboard_hardware.png` | `python run.py demo --hardware` driving the firmware on the simulated board (`tools/virtual_board`): speed at *real time*, the status bar reporting the board live with two toy cars sensed and one remote call; the ambulance came from remote button A |

The live KPI cards describe the episode on screen (one seed, still running), so they differ
from the three-seed benchmark in `outputs/benchmark_summary.md`.

To refresh the stills, run `python run.py demo` and take screenshots, or run
`python tools/e2e_browser.py --local`, which saves desktop, phone and wake-screen captures
in `outputs/e2e/`. To refresh the animation, run `python tools/record_gif.py`; pass
`--python` a venv made from `requirements-deploy.txt` to record the hosted stack.
