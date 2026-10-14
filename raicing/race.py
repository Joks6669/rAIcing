"""Race control: qualifying and race sessions, classification, points and replays."""
from __future__ import annotations

import gzip
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .brains.spec import Brain
from .config import COMPOUNDS, Regulations
from .drivers.reference import ReferenceDriver
from .physics.car import CarSpec, Setup, build_spec
from .sensors import observe
from .track.geometry import Track
from .weather import WeatherScript
from .world import ACT_DIM, DNF, DSQ, FINISHED, PIT, RACING, STATUS_NAMES, SessionConfig, World


@dataclass
class Entry:
    """One car on the grid."""
    entry_id: str
    team_id: str
    team_name: str
    driver_name: str
    brain: Brain | None = None          # neural pilot (team entries)
    reference: str | None = None        # reference personality (system entries)
    spec: CarSpec | None = None
    setup: Setup = field(default_factory=Setup)
    color: str = "#888888"
    driver_id: str = ""
    generation: int | None = None


class Pilots:
    """Runs every car's controller once per decision."""

    def __init__(self, world: World, entries: list[Entry], laps: int | None = None):
        self.w = world
        self.entries = entries
        self.brain_cars = [i for i, e in enumerate(entries) if e.brain is not None]
        self.states = {i: entries[i].brain.init_state(1) for i in self.brain_cars}
        ref_cars = [i for i, e in enumerate(entries) if e.brain is None]
        self.ref = None
        if ref_cars:
            pers = [entries[i].reference or "pro" for i in ref_cars]
            self.ref = ReferenceDriver(world, ref_cars, pers, laps=laps)
        self.ref_cars = ref_cars
        self.decision_time = np.zeros(len(entries))

    def act(self) -> np.ndarray:
        w = self.w
        out = np.zeros((w.n, ACT_DIM))
        live = [i for i in self.brain_cars if w.status[i] == RACING]
        if live:
            obs = observe(w, np.array(live))
            for row, i in enumerate(live):
                t0 = time.perf_counter()
                a, self.states[i] = self.entries[i].brain.act(obs[row:row + 1], self.states[i])
                self.decision_time[i] += time.perf_counter() - t0
                out[i] = a[0]
        if self.ref is not None:
            out[self.ref_cars] = self.ref.act()
        return out


def estimate_lap_time(track: Track, regs: Regulations) -> float:
    base = build_spec(regs)
    prof = track.speed_profile(base.mu * 0.92, base.mass, base.cla, base.power, base.cda)
    return float(np.sum(track.ds / np.maximum(prof, 5.0)))


def race_laps(track: Track, regs: Regulations) -> int:
    rr = regs.race
    lap = estimate_lap_time(track, regs)
    return int(np.clip(round(rr.target_race_time / lap), rr.min_laps, rr.max_laps))


def _specs(entries: list[Entry], regs: Regulations) -> list[CarSpec]:
    return [e.spec or build_spec(regs, setup=e.setup) for e in entries]


def _car_meta(entries: list[Entry]) -> list[dict[str, Any]]:
    return [{"car": i, "entry_id": e.entry_id, "team_id": e.team_id, "team": e.team_name,
             "driver": e.driver_name, "driver_id": e.driver_id, "color": e.color,
             "generation": e.generation, "kind": "brain" if e.brain is not None else "reference"}
            for i, e in enumerate(entries)]


# ---------------------------------------------------------------------- qualifying
def run_qualifying(track: Track, entries: list[Entry], regs: Regulations | None = None,
                   weather: WeatherScript | None = None, seed: int = 0, record: bool = False,
                   time_limit: float | None = None) -> dict[str, Any]:
    regs = regs or Regulations()
    lap_est = estimate_lap_time(track, regs)
    sess = SessionConfig(kind="quali", interactions=False, record=record, record_every=20,
                         max_time=time_limit or (3.0 * lap_est + 60.0))
    w = World(track, _specs(entries, regs), regs, weather, sess, teams=None, seed=seed)
    w.setup_quali([e.setup.quali_compound for e in entries])
    pilots = Pilots(w, entries)
    while not w.done():
        w.step(pilots.act())
    rows = []
    for i, e in enumerate(entries):
        t = w.lap_times[i][0] if (w.status[i] == FINISHED and w.lap_times[i]) else None
        rows.append({"car": i, "entry_id": e.entry_id, "team_id": e.team_id, "team": e.team_name,
                     "driver": e.driver_name, "driver_id": e.driver_id, "time": t,
                     "status": "timed" if t is not None else "no_time",
                     "reason": w.reason[i] if t is None else "", "compound": e.setup.quali_compound})
    timed = sorted([r for r in rows if r["time"] is not None], key=lambda r: r["time"])
    untimed = [r for r in rows if r["time"] is None]
    order = timed + untimed
    best = timed[0]["time"] if timed else None
    for pos, r in enumerate(order, start=1):
        r["pos"] = pos
        r["gap"] = (r["time"] - best) if (r["time"] is not None and best is not None) else None
    out = {"kind": "quali", "classification": order, "sim_time": w.t,
           "cars": _car_meta(entries)}
    if record:
        out["replay"] = _replay(w, entries, "quali", None)
    return out


# ---------------------------------------------------------------------- race
def run_race(track: Track, entries: list[Entry], grid: list[int], laps: int, regs: Regulations | None = None,
             weather: WeatherScript | None = None, seed: int = 0, record: bool = True,
             progress_cb=None) -> dict[str, Any]:
    """Run a race. ``grid`` lists car indices from pole position backwards."""
    regs = regs or Regulations()
    lap_est = estimate_lap_time(track, regs)
    sess = SessionConfig(kind="race", laps=laps, interactions=True, record=record, record_every=20,
                         max_time=regs.race.max_time_factor * max(laps * lap_est, 60.0))
    teams = [e.team_id for e in entries]
    team_ids = {t: k for k, t in enumerate(dict.fromkeys(teams))}
    w = World(track, _specs(entries, regs), regs, weather, sess, teams=[team_ids[t] for t in teams], seed=seed)
    w.setup_grid(grid, [e.setup.start_compound for e in entries])
    pilots = Pilots(w, entries, laps=laps)
    last_cb = 0.0
    while not w.done():
        w.step(pilots.act())
        if progress_cb is not None and w.t - last_cb >= 5.0:
            last_cb = w.t
            progress_cb(w)
    # anything still running at the time limit is classified on distance
    for i in np.nonzero(np.isin(w.status, (RACING, PIT)))[0]:
        w._finish(int(i), w.t, timed=False)
    w._update_positions()
    inv_team = {k: t for t, k in team_ids.items()}
    result = classify(w, entries, laps)
    result["team_penalties"] = {inv_team[k]: v for k, v in w.team_penalties.items()}
    result["grid"] = [entries[i].entry_id for i in grid]
    result["laps"] = laps
    result["sim_time"] = w.t
    result["cars"] = _car_meta(entries)
    result["decision_time"] = {entries[i].entry_id: float(pilots.decision_time[i]) for i in range(len(entries))}
    if record:
        result["replay"] = _replay(w, entries, "race", laps)
    return result


def classify(w: World, entries: list[Entry], laps: int) -> dict[str, Any]:
    order = np.argsort(w.position)
    L = w.track.length
    pts_table = list(w.regs.race.points)
    rows = []
    leader_time = None
    leader_laps = None
    points_rank = 0
    for pos, i in enumerate(order, start=1):
        i = int(i)
        e = entries[i]
        st = int(w.status[i])
        laps_done = int(max(np.floor(w.max_progress[i] / L + 1e-9), 0))
        timed = st == FINISHED and not np.isnan(w.finish_time[i])
        total = float(w.finish_time[i] + w.penalty_time[i]) if timed else None
        if pos == 1:
            leader_time, leader_laps = total, laps_done
        gap = None
        if total is not None and leader_time is not None:
            if laps_done == leader_laps:
                gap = f"+{total - leader_time:.3f}s" if pos > 1 else ""
            else:
                gap = f"+{leader_laps - laps_done} tour" + ("s" if leader_laps - laps_done > 1 else "")
        elif st == FINISHED:
            gap = f"+{(leader_laps or laps_done) - laps_done} tour(s)"
        pts = 0
        if st == FINISHED:
            if points_rank < len(pts_table):
                pts = pts_table[points_rank]
            points_rank += 1
        lt = w.lap_times[i]
        rows.append({
            "pos": pos, "car": i, "entry_id": e.entry_id, "team_id": e.team_id, "team": e.team_name,
            "driver": e.driver_name, "driver_id": e.driver_id, "generation": e.generation,
            "status": STATUS_NAMES[st], "reason": w.reason[i], "laps": laps_done, "time": total,
            "penalty": float(w.penalty_time[i]), "gap": gap, "best_lap": float(min(lt)) if lt else None,
            "lap_times": [round(x, 3) for x in lt], "pits": int(w.pit_stops[i]), "points": pts,
            "end_time": None if np.isnan(w.end_time[i]) else float(w.end_time[i]),
        })
    fastest = None
    for r in rows:
        if r["best_lap"] is not None and (fastest is None or r["best_lap"] < fastest["time"]):
            fastest = {"entry_id": r["entry_id"], "driver": r["driver"], "time": r["best_lap"]}
    incidents = [{"t": round(inc.t, 2), "cars": [entries[inc.a].entry_id, entries[inc.b].entry_id],
                  "kind": inc.kind, "fault": entries[inc.fault].entry_id if inc.fault is not None else None,
                  "impulse": round(float(inc.impulse), 1), "closing": round(float(inc.closing), 2),
                  "penalised": inc.penalised} for inc in w.incidents]
    return {"kind": "race", "classification": rows, "fastest_lap": fastest, "incidents": incidents,
            "events": _named_events(w.events, entries)}


def _named_events(events: list[dict], entries: list[Entry]) -> list[dict]:
    out = []
    for ev in events:
        e = dict(ev)
        if "car" in e and isinstance(e["car"], (int, np.integer)):
            e["entry_id"] = entries[int(e["car"])].entry_id
        out.append(e)
    return out


def _replay(w: World, entries: list[Entry], kind: str, laps: int | None) -> dict[str, Any]:
    return {
        "format": "raicing-replay/1",
        "kind": kind,
        "laps": laps,
        "dt": w.dt * w.session.record_every,
        "track": w.track.to_dict(decimate=2),
        "cars": _car_meta(entries),
        "frame_fields": ["x", "y", "psi", "speed", "status", "compound", "wear", "temp", "lap", "pos",
                         "damage", "steer", "pedal"],
        "status_names": STATUS_NAMES,
        "compounds": list(COMPOUNDS),
        "frames": w.frames,
        "weather": w.weather.to_dict(),
        "weather_frames": w.weather_frames,
        "events": _named_events(w.events, entries),
    }


def save_replay(replay: dict[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump(replay, f, separators=(",", ":"))
    return p


def load_replay(path: str | Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)
