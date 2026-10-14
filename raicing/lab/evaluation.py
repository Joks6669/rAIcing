"""Standardised evaluation runs (used by the Driver Rating and the regulation bench)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Union

import numpy as np

from ..brains.spec import Brain
from ..config import Regulations
from ..drivers.reference import ReferenceDriver
from ..physics.car import CarSpec, build_spec
from ..race import Entry, estimate_lap_time, run_race
from ..sensors import observe
from ..track import Track, generate_track
from ..weather import WeatherScript
from ..world import ACT_DIM, RACING, SessionConfig, World

Pilot = Union[Brain, str]

LAB_SEED_BASE = 10_000_000
VALIDATION_SEED_BASE = 20_000_000


def lab_track(k: int) -> Track:
    return generate_track(LAB_SEED_BASE + int(k))


def validation_track(k: int) -> Track:
    return generate_track(VALIDATION_SEED_BASE + int(k))


@dataclass
class SoloResult:
    lap_times: list[float]
    crashed: bool
    reason: str
    wear: float
    sim_time: float

    @property
    def best(self) -> float | None:
        return min(self.lap_times) if self.lap_times else None

    def to_dict(self) -> dict:
        return {"lap_times": [round(x, 3) for x in self.lap_times], "crashed": self.crashed,
                "reason": self.reason, "wear": round(self.wear, 3), "best": self.best}


def wet_weather(level: float = 0.8, ambient: float = 16.0) -> WeatherScript:
    return WeatherScript(times=[0.0], rain=[level], ambient=ambient, initial_wetness=level)


def run_solo(pilot: Pilot, track: Track, regs: Regulations | None = None, laps: int = 3,
             compound: str = "medium", weather: WeatherScript | None = None, spec: CarSpec | None = None,
             start_temp: float = 80.0, seed: int = 0) -> SoloResult:
    """One car alone: a rolling start 200 m before the line, then ``laps`` timed laps."""
    regs = regs or Regulations()
    spec = spec or build_spec(regs)
    w = World(track, [spec], regs, weather, SessionConfig(kind="lab", interactions=False), seed=seed)
    w.place_car(0, track.length - 200.0, 0.0, 30.0, compound=compound, tyre_temp=start_temp, progress=-200.0)
    max_t = laps * estimate_lap_time(track, regs) * 2.5 + 60.0
    if isinstance(pilot, str):
        drv = ReferenceDriver(w, [0], pilot)
        act = drv.act
    else:
        state = pilot.init_state(1)

        def act():
            nonlocal state
            a, state = pilot.act(observe(w, np.array([0])), state)
            return a
    reason = ""
    while w.status[0] == RACING and len(w.lap_times[0]) < laps:
        w.step(act())
        if w.t > max_t:
            reason = "too_slow"
            break
    crashed = w.status[0] != RACING or reason != ""
    if w.status[0] != RACING:
        reason = w.reason[0]
    return SoloResult(list(w.lap_times[0]), crashed, reason, float(max(w.wear_f[0], w.wear_r[0])), w.t)


def run_traffic(pilot: Pilot, track: Track, regs: Regulations | None = None, laps: int = 4,
                opponents: list[str] | None = None, seed: int = 0) -> dict:
    """The pilot starts last behind reference cars. Returns positions gained and incidents."""
    regs = regs or Regulations()
    opponents = opponents or ["prudent", "pro", "pneus", "cooperatif", "debutant"]
    entries = [Entry(f"ref#{k}", f"ref{k}", f"Référence {k}", p, reference=p) for k, p in enumerate(opponents)]
    me = Entry("pilot", "pilot", "Pilote", "Pilote")
    if isinstance(pilot, str):
        me.reference = pilot
    else:
        me.brain = pilot
    entries.append(me)
    grid = list(range(len(entries)))
    res = run_race(track, entries, grid, laps, regs, seed=seed, record=False)
    row = next(r for r in res["classification"] if r["entry_id"] == "pilot")
    at_fault = sum(1 for inc in res["incidents"] if inc["fault"] == "pilot" and inc["penalised"])
    start = len(entries)
    gained = start - row["pos"] if row["status"] == "finished" else 0
    return {"pos": row["pos"], "start": start, "gained": gained, "status": row["status"], "at_fault": at_fault,
            "penalty_points": res["team_penalties"].get("pilot", 0)}


def act_fn_for(pilot: Pilot, world: World, cars: list[int]):
    """Return a callable producing (len(cars), ACT_DIM) actions for ``cars`` of ``world``."""
    if isinstance(pilot, str):
        drv = ReferenceDriver(world, cars, pilot)
        return drv.act
    states = {c: pilot.init_state(1) for c in cars}

    def act():
        out = np.zeros((len(cars), ACT_DIM))
        obs = observe(world, np.array(cars))
        for k, c in enumerate(cars):
            if world.status[c] == RACING:
                a, states[c] = pilot.act(obs[k:k + 1], states[c])
                out[k] = a[0]
        return out
    return act
