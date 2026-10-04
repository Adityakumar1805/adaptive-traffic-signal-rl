#!/usr/bin/env python3
"""Single entry point for the Adaptive Traffic Signal Control project.

Usage
-----
    python run.py demo            # train-if-needed, then open the live dashboard
    python run.py demo --hardware # ...driving the Arduino signal model (docs/HARDWARE.md)
    python run.py hwtest          # check a newly built signal board, part by part
    python run.py train --quick   # fast training (a few minutes) -> atsc_<grid>_quick.pt
    python run.py train --overwrite   # full training; REPLACES the shipped checkpoint
    python run.py eval            # benchmark RL vs fixed-time vs max-pressure + plots
    python run.py sim             # watch the RL controller in SUMO-GUI (needs SUMO)
    python run.py doctor          # environment / dependency check

All behaviour is driven by config.yaml. Run from the project root. Add --debug to any
command to see the full traceback of an error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _load_cfg(args):
    from atsc.config import load_config
    return load_config(args.config)


def cmd_doctor(args) -> int:
    from atsc.logging_utils import setup_logging
    setup_logging()
    import platform

    from atsc import __version__
    print("=" * 64)
    print(f"  ATSC environment check  (version {__version__})")
    print("=" * 64)
    print(f"  Python           : {platform.python_version()}  ({platform.system()})")

    def check(name, ok, detail=""):
        mark = "OK " if ok else "-- "
        print(f"  [{mark}] {name:16s} {detail}")

    import importlib.util as u
    have = lambda m: u.find_spec(m) is not None
    check("numpy", have("numpy"))
    check("torch", have("torch"), "(optional - NumPy fallback used if absent)")
    check("gymnasium", have("gymnasium"), "(optional)")
    check("fastapi", have("fastapi"), "(optional - stdlib server used if absent)")
    check("matplotlib", have("matplotlib"), "(needed for eval plots)")
    check("pandas", have("pandas"), "(needed for eval tables)")
    check("pyserial", have("serial"), "(optional - only for the Arduino signal model)")
    if have("serial"):
        from atsc.hw.link import list_ports
        boards = [f"{dev} ({desc})" for dev, desc, board in list_ports() if board]
        check("signal board", bool(boards), ", ".join(boards) or "(none plugged in)")

    from atsc.sim.sumo_detect import detect_sumo
    info = detect_sumo()
    check("SUMO", info.found, f"(optional; SUMO_HOME={info.sumo_home or 'unset'})")

    try:
        cfg = _load_cfg(args)
        print(f"  config.yaml      : OK  ({cfg.n_intersections} intersections, "
              f"{cfg.n_phases} phases, backend={cfg.backend})")
        from atsc.control.rl_controller import default_checkpoint_path
        ck = default_checkpoint_path(cfg)
        quick = ck.with_name(ck.stem + "_quick.pt")
        check("pretrained model", ck.exists() or quick.exists(),
              f"({ck.name if ck.exists() else quick.name if quick.exists() else 'none'})")
    except Exception as exc:
        print(f"  config.yaml      : ERROR - {exc}")
        return 1
    print("=" * 64)
    print("  Ready. Next:  python run.py demo")
    print("=" * 64)
    return 0


def cmd_train(args) -> int:
    from atsc.logging_utils import setup_logging
    setup_logging()
    cfg = _load_cfg(args)
    from atsc.control.rl_controller import default_checkpoint_path
    target = default_checkpoint_path(cfg)
    if not args.quick and target.exists() and not args.overwrite:
        print(f"{target.relative_to(ROOT) if target.is_relative_to(ROOT) else target} already "
              "exists: it is the shipped model that every published result and the live demo "
              "come from, and outputs/logs/train.csv is its training log.\n"
              "Training again produces a different model (training is not bit-reproducible).\n"
              "  python run.py train --quick       a few-minute model in a separate *_quick.pt file\n"
              "  python run.py train --overwrite   replace the shipped model and its log on purpose")
        return 1
    from atsc.train.trainer import train_main
    path = train_main(cfg, quick=args.quick)
    print(f"\nCheckpoint saved: {path}")
    try:
        from atsc.eval.plots import plot_training_curve
        log_csv = ROOT / cfg.train.log_dir / ("train_quick.csv" if args.quick else "train.csv")
        name = "training_curve_quick.png" if args.quick else "training_curve.png"
        p = plot_training_curve(str(log_csv), str(ROOT / cfg.eval.out_dir), name=name)
        if p:
            print(f"Training curve : {p}")
    except Exception as exc:  # plotting is a bonus; the checkpoint is what matters
        print(f"(training curve not plotted: {exc})")
    return 0


def cmd_eval(args) -> int:
    from atsc.logging_utils import setup_logging
    setup_logging()
    cfg = _load_cfg(args)
    from atsc.control.rl_controller import default_checkpoint_path
    ck = default_checkpoint_path(cfg)
    quick = ck.with_name(ck.stem + "_quick.pt")
    if not ck.exists() and not quick.exists():
        print("No trained model found - training a quick one first...")
        from atsc.train.trainer import train_main
        train_main(cfg, quick=True)
    from atsc.eval.benchmark import run_benchmark
    from atsc.eval.plots import make_all_plots
    backend = args.backend or str(cfg.get_path("eval.backend") or "mini")
    out_dir = ROOT / cfg.eval.out_dir
    if backend != "mini":
        # the published CSVs and plots are the built-in simulator's: keep them intact
        out_dir = out_dir / backend
        print(f"Benchmarking on the '{backend}' backend -> {out_dir} "
              "(the published results in outputs/ come from the built-in simulator)")
    df = run_benchmark(cfg, seeds=args.seeds, backend_name=backend, out_dir=str(out_dir))
    if not df.empty:
        paths = make_all_plots(df, str(out_dir))
        print("\nPlots written:")
        for p in paths:
            print(f"  {p}")
    return 0


def cmd_sim(args) -> int:
    from atsc.logging_utils import setup_logging, get_logger
    setup_logging()
    log = get_logger("sim")
    cfg = _load_cfg(args)
    from atsc.sim.sumo_detect import detect_sumo, install_instructions
    info = detect_sumo()
    if not info.found:
        print(install_instructions())
        return 1
    log.info("Launching SUMO-GUI with the RL controller (scenario=%s)...", args.scenario)
    from atsc.sim import build_topology
    from atsc.sim.sumo_backend import SumoBackend
    from atsc.envs.traffic_env import MultiAgentTrafficEnv
    from atsc.control import build_controller
    topo = build_topology(cfg)
    backend = SumoBackend(cfg, topo, use_gui=True)
    env = MultiAgentTrafficEnv(cfg, backend=backend, backend_name="sumo", topo=topo)
    ctrl = build_controller("rl", cfg, topo, allow_untrained=True)
    env.reset(scenario=args.scenario, seed=cfg.seed)
    try:
        while env.agents:
            env.step(ctrl.act(env))
    finally:
        env.close()
    return 0


def cmd_demo(args) -> int:
    # First, before NumPy loads: the dashboard needs one BLAS thread, not a spinning pool.
    from atsc.threads import limit_math_threads
    limit_math_threads()
    from atsc.logging_utils import setup_logging, get_logger
    setup_logging()
    log = get_logger("demo")
    cfg = _load_cfg(args)
    from atsc.control.rl_controller import default_checkpoint_path
    ck = default_checkpoint_path(cfg)
    quick = ck.with_name(ck.stem + "_quick.pt")
    if not ck.exists() and not quick.exists():
        log.info("No pretrained model found - training a quick one (a few minutes)...")
        from atsc.train.trainer import train_main
        train_main(cfg, quick=True)
    hardware = None
    if getattr(args, "hardware", False):
        import importlib.util
        from atsc.hw import hardware_settings
        hardware = {"enabled": True, "port": args.port, "mirror": args.mirror,
                    "realtime": not args.fast}
        port = str(hardware_settings(cfg, hardware)["port"]).strip().lower()
        if port != "loopback" and importlib.util.find_spec("serial") is None:
            log.error("--hardware needs pyserial:  pip install pyserial==3.5")
            return 1
    log.info("Starting the live dashboard. Press Ctrl+C to stop.")
    from atsc.dashboard.server import run_dashboard
    run_dashboard(config_path=args.config, hardware=hardware)
    return 0


def cmd_hwtest(args) -> int:
    from atsc.hw.selftest import run_selftest
    return run_selftest(port=args.port or "auto", listen_s=float(args.seconds),
                        walk=not args.no_walk)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="run.py", description="Adaptive Traffic Signal Control (RL)")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    parser.add_argument("--debug", action="store_true", help="show full tracebacks on errors")
    sub = parser.add_subparsers(dest="command")

    p_demo = sub.add_parser("demo", help="train-if-needed then open the live dashboard")
    p_demo.add_argument("--hardware", action="store_true",
                        help="drive the Arduino signal model (docs/HARDWARE.md)")
    p_demo.add_argument("--port", default=None,
                        help="board's serial port, e.g. COM5 or /dev/cu.usbserial-110 "
                             "(default: hardware.port in config.yaml, 'auto' finds it)")
    p_demo.add_argument("--mirror", choices=["rl", "fixed"], default=None,
                        help="which grid the lamps show (default rl: the AI)")
    p_demo.add_argument("--fast", action="store_true",
                        help="start at the normal dashboard speed instead of real time")
    p_hw = sub.add_parser("hwtest", help="test a newly built signal board (lamps, sensors, remote)")
    p_hw.add_argument("--port", default=None, help="serial port (default: auto-detect)")
    p_hw.add_argument("--seconds", type=float, default=60.0,
                      help="how long to listen for sensors and buttons (default 60)")
    p_hw.add_argument("--no-walk", action="store_true", help="skip the lamp walk")
    p_train = sub.add_parser("train", help="train the RL controller")
    p_train.add_argument("--quick", action="store_true", help="fast, few-minute training")
    p_train.add_argument("--overwrite", action="store_true",
                         help="allow a full training run to replace the shipped checkpoint")
    p_eval = sub.add_parser("eval", help="benchmark RL vs baselines and make plots")
    p_eval.add_argument("--seeds", type=int, nargs="*", default=None)
    p_eval.add_argument("--backend", choices=["mini", "sumo", "auto"], default=None,
                        help="simulator for the benchmark (default: eval.backend in config.yaml)")
    p_sim = sub.add_parser("sim", help="watch the RL controller in SUMO-GUI")
    p_sim.add_argument("--scenario", default="high")
    sub.add_parser("doctor", help="check the environment and dependencies")

    args = parser.parse_args()
    if not args.command:
        args.command = "demo"

    dispatch = {
        "demo": cmd_demo, "train": cmd_train, "eval": cmd_eval,
        "sim": cmd_sim, "doctor": cmd_doctor, "hwtest": cmd_hwtest,
    }
    try:
        return dispatch[args.command](args)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except Exception as exc:
        if args.debug:
            raise
        print(f"Error ({type(exc).__name__}): {exc}\n(run again with --debug for the full traceback)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
