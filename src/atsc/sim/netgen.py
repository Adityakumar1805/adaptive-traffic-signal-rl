"""Generate SUMO network + route files for the configured grid.

Produces, under ``scenarios/generated/<grid>/``:
  * ``grid.nod.xml``  — nodes (traffic-light junctions + boundary dead-ends)
  * ``grid.edg.xml``  — edges (bidirectional links between junctions)
  * ``grid.net.xml``  — compiled network (built by ``netconvert``)
  * ``<scenario>.rou.xml`` — vehicle routes for a density scenario (deterministic)
  * ``<scenario>.sumocfg``  — SUMO config tying net + routes together

We deliberately write only nodes + edges and let ``netconvert --tls.guess`` derive the
lane-to-lane connections and a default traffic-light program: no ``.con.xml`` / ``.tll.xml``
is emitted. The generated program is never actually executed, because
:class:`~atsc.sim.sumo_backend.SumoBackend` overrides the light state every step with
``setRedYellowGreenState`` built from the approach set our phase FSM asks for. That keeps the
same RL action space driving either backend without having to keep a hand-written phase file
index-aligned with :func:`atsc.sim.backend._phase_set`.

If ``netconvert`` is unavailable we still emit the XML sources and raise a clear error
only when the SUMO backend actually needs the compiled ``.net.xml``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from atsc.config import project_root
from atsc.sim.backend import NetworkTopo, build_topology

# SUMO link direction letters for edges we create between grid nodes.
# We model one lane per approach by default; netconvert infers signal logic we override.


def scenario_dir(cfg) -> Path:
    root = project_root() / "scenarios" / "generated"
    grid = f"grid_{cfg.network.grid_rows}x{cfg.network.grid_cols}"
    d = root / grid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _node_xml(topo: NetworkTopo, cfg) -> str:
    link = float(cfg.network.link_length_m)
    lines = ['<nodes>']
    # internal signalised junctions
    for it in topo:
        lines.append(
            f'  <node id="{it.id}" x="{it.x:.1f}" y="{it.y:.1f}" type="traffic_light"/>'
        )
    # boundary "cap" nodes so edges have somewhere to originate/terminate
    for it in topo:
        for approach, neigh in it.neighbors.items():
            if neigh is None:
                bx, by = _boundary_xy(it.x, it.y, approach, link)
                lines.append(
                    f'  <node id="{it.id}_{approach}cap" x="{bx:.1f}" y="{by:.1f}" '
                    f'type="priority"/>'
                )
    lines.append('</nodes>')
    return "\n".join(lines)


def _boundary_xy(x: float, y: float, approach: str, link: float) -> Tuple[float, float]:
    if approach == "N":
        return x, y - link
    if approach == "S":
        return x, y + link
    if approach == "W":
        return x - link, y
    return x + link, y


def _edge_xml(topo: NetworkTopo, cfg) -> str:
    lanes = int(cfg.network.lanes_per_approach)
    speed = float(cfg.sim.free_speed_mps)
    lines = ['<edges>']

    def edge(frm: str, to: str) -> str:
        eid = f"{frm}__{to}"
        return (f'  <edge id="{eid}" from="{frm}" to="{to}" '
                f'priority="1" numLanes="{lanes}" speed="{speed:.1f}"/>')

    added = set()
    for it in topo:
        # internal links to neighbours (both directions handled as we iterate all nodes)
        for approach, neigh in it.neighbors.items():
            if neigh is not None:
                key = (it.id, neigh)
                if key not in added:
                    lines.append(edge(it.id, neigh))
                    added.add(key)
            else:
                cap = f"{it.id}_{approach}cap"
                # inbound (cap -> junction) and outbound (junction -> cap)
                lines.append(edge(cap, it.id))
                lines.append(edge(it.id, cap))
    lines.append('</edges>')
    return "\n".join(lines)


def build_net(cfg, force: bool = False) -> Path:
    """Write node/edge XML and compile them with ``netconvert`` into ``grid.net.xml``.

    Returns the path to the compiled network. Raises a clear ``RuntimeError`` if
    ``netconvert`` is not installed (only relevant when the SUMO backend is requested).
    """
    import shutil

    topo = build_topology(cfg)
    d = scenario_dir(cfg)
    nod = d / "grid.nod.xml"
    edg = d / "grid.edg.xml"
    net = d / "grid.net.xml"

    nod.write_text("<?xml version='1.0'?>\n" + _node_xml(topo, cfg), encoding="utf-8")
    edg.write_text("<?xml version='1.0'?>\n" + _edge_xml(topo, cfg), encoding="utf-8")

    if net.exists() and not force:
        return net

    netconvert = shutil.which("netconvert")
    if netconvert is None:
        raise RuntimeError(
            "netconvert (part of SUMO) was not found on PATH, so the SUMO .net.xml "
            "cannot be compiled. Install SUMO and set SUMO_HOME, or use the built-in "
            "backend (backend: mini in config.yaml)."
        )
    cmd = [
        netconvert,
        "--node-files", str(nod),
        "--edge-files", str(edg),
        "--output-file", str(net),
        "--tls.guess", "true",
        "--tls.default-type", "static",
        "--no-turnarounds", "true",
        "--offset.disable-normalization", "true",
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return net


def build_routes(cfg, scenario: str, seed: int) -> Path:
    """Generate a deterministic ``.rou.xml`` for a density scenario.

    Uses the same Poisson arrival rates as the built-in simulator so the two backends
    present comparable traffic (statistically, not vehicle for vehicle). Vehicles are emitted
    on straight corridors across the grid, each as an explicit ``<trip>`` with from/to
    edges. Unlike the built-in simulator there are no turning trips (``turn_prob`` is not
    used here), and vehicles start on a 200 m entry link instead of at the stop line.
    """
    topo = build_topology(cfg)
    params = cfg.scenario_params(scenario)
    rng = np.random.default_rng(seed)
    horizon = int(cfg.eval.episode_seconds)
    d = scenario_dir(cfg)
    rou = d / f"{scenario}.rou.xml"

    lanes = int(cfg.network.lanes_per_approach)
    base = float(params["base_arrival_vps"])
    scale = float(params["arrival_scale"])
    arterial = float(params.get("arterial_boost", 1.0))

    # boundary entries -> outbound cap edges (destinations) for straight corridors
    entries: List[Tuple[str, str]] = []
    for it in topo:
        for approach, neigh in it.neighbors.items():
            if neigh is None:
                entries.append((it.id, approach))

    trips: List[str] = []
    vid = 0
    for t in range(horizon):
        for (iid, approach) in entries:
            boost = arterial if approach in ("E", "W") else 1.0
            rate = base * scale * boost * lanes
            if params.get("time_varying", False):
                # gaussian rush profile
                import math
                center = params.get("peak_center_frac", 0.5) * horizon
                width = max(1.0, params.get("peak_width_frac", 0.2) * horizon)
                rate *= 0.6 + 1.0 * math.exp(-0.5 * ((t - center) / width) ** 2)
            n = rng.poisson(rate)
            for _ in range(int(n)):
                from_edge = f"{iid}_{approach}cap__{iid}"
                to_edge = _straight_exit_edge(topo, iid, approach)
                if to_edge is None:
                    continue
                trips.append(
                    f'  <trip id="v{vid}" depart="{t}.00" from="{from_edge}" '
                    f'to="{to_edge}" departLane="best" departSpeed="max"/>'
                )
                vid += 1

    header = (
        "<?xml version='1.0'?>\n<routes>\n"
        '  <vType id="car" accel="2.6" decel="4.5" sigma="0.5" length="5" '
        'minGap="2.5" maxSpeed="13.9"/>\n'
        '  <vType id="emergency" accel="3.0" decel="5.0" sigma="0.3" length="6" '
        'minGap="2.5" maxSpeed="18.0" vClass="emergency" guiShape="emergency" '
        'color="1,0,0"/>\n'
    )
    # trips must be sorted by depart for SUMO
    body = "\n".join(trips)
    rou.write_text(header + body + "\n</routes>\n", encoding="utf-8")

    _write_sumocfg(cfg, scenario, d)
    return rou


def straight_corridor_edges(topo: NetworkTopo, entry_iid: str, entry_approach: str) -> List[str]:
    """The full, connected edge list of a straight corridor: entry cap -> junctions -> exit cap."""
    from atsc.sim.backend import APPROACH_TO_TRAVEL
    delta = {"N": (-1, 0), "S": (1, 0), "E": (0, 1), "W": (0, -1)}
    it = topo.get(entry_iid)
    r, c = it.row, it.col
    travel = APPROACH_TO_TRAVEL[entry_approach]
    edges = [f"{entry_iid}_{entry_approach}cap__{entry_iid}"]
    prev = entry_iid
    while True:
        dr, dc = delta[travel]
        nr, nc = r + dr, c + dc
        if not (0 <= nr < topo.grid_rows and 0 <= nc < topo.grid_cols):
            edges.append(f"{prev}__{prev}_{travel}cap")
            return edges
        nxt = f"J{nr}_{nc}"
        edges.append(f"{prev}__{nxt}")
        prev, r, c = nxt, nr, nc


def _straight_exit_edge(topo: NetworkTopo, entry_iid: str, entry_approach: str) -> Optional[str]:
    """Find the outbound cap edge where a straight corridor from an entry leaves the grid."""
    from atsc.sim.backend import APPROACH_TO_TRAVEL
    delta = {"N": (-1, 0), "S": (1, 0), "E": (0, 1), "W": (0, -1)}
    it = topo.get(entry_iid)
    r, c = it.row, it.col
    travel = APPROACH_TO_TRAVEL[entry_approach]
    rows, cols = topo.grid_rows, topo.grid_cols
    prev = entry_iid
    while True:
        dr, dc = delta[travel]
        nr, nc = r + dr, c + dc
        if not (0 <= nr < rows and 0 <= nc < cols):
            # leaves grid at current node via `travel` side
            exit_side = travel
            return f"{prev}__{prev}_{exit_side}cap"
        prev = f"J{nr}_{nc}"
        r, c = nr, nc


def _write_sumocfg(cfg, scenario: str, d: Path) -> Path:
    cfgfile = d / f"{scenario}.sumocfg"
    net = "grid.net.xml"
    rou = f"{scenario}.rou.xml"
    step = float(cfg.sim.step_length_s)
    end = int(cfg.eval.episode_seconds)
    xml = f"""<?xml version='1.0'?>
<configuration>
  <input>
    <net-file value="{net}"/>
    <route-files value="{rou}"/>
  </input>
  <time>
    <begin value="0"/>
    <end value="{end}"/>
    <step-length value="{step}"/>
  </time>
  <processing>
    <time-to-teleport value="-1"/>
    <collision.action value="warn"/>
  </processing>
  <report>
    <no-step-log value="true"/>
    <duration-log.disable value="true"/>
  </report>
</configuration>
"""
    cfgfile.write_text(xml, encoding="utf-8")
    return cfgfile


def generate_all(cfg, seed: int = 0) -> Dict[str, Path]:
    """Generate the network and route files for every configured scenario."""
    out: Dict[str, Path] = {}
    try:
        out["net"] = build_net(cfg)
    except RuntimeError:
        # netconvert missing: still emit sources; SUMO backend will report clearly.
        out["net"] = scenario_dir(cfg) / "grid.net.xml"
    for scen in cfg.scenarios.keys():
        out[scen] = build_routes(cfg, scen, seed)
    return out
