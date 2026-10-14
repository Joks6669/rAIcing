"""Driver Rating: a standardised, purely informative evaluation of a pilot.

Six components on fixed validation circuits, each normalised against the reference driver
("pro"): speed, consistency, survival, rain, tyre management and traffic. 50 means "as good
as the reference"; 100 means clearly better. The overall score comes with an uncertainty
interval derived from the spread between circuits.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field

import numpy as np

from ..config import Regulations
from ..lab.evaluation import Pilot, run_solo, run_traffic, validation_track, wet_weather

WEIGHTS = {"speed": 0.30, "consistency": 0.15, "survival": 0.15, "rain": 0.10, "tyres": 0.10, "traffic": 0.20}
LABELS = {"speed": "Vitesse pure", "consistency": "Régularité", "survival": "Survie",
          "rain": "Pilotage sous la pluie", "tyres": "Gestion des pneus", "traffic": "Compétition et trafic"}


@dataclass(frozen=True)
class RatingConfig:
    speed_tracks: tuple[int, ...] = (0, 1, 2)
    speed_laps: int = 3
    rain_tracks: tuple[int, ...] = (3,)
    rain_laps: int = 2
    tyre_track: int = 4
    tyre_laps: int = 8
    traffic_track: int = 5
    traffic_laps: int = 3

    @classmethod
    def quick(cls) -> "RatingConfig":
        return cls(speed_tracks=(0,), speed_laps=2, rain_tracks=(3,), rain_laps=1, tyre_laps=4, traffic_laps=2)


@dataclass
class Rating:
    overall: float
    ci: tuple[float, float]
    components: dict[str, float]
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"overall": round(self.overall, 1), "ci": [round(self.ci[0], 1), round(self.ci[1], 1)],
                "components": {k: round(v, 1) for k, v in self.components.items()},
                "labels": LABELS, "details": self.details}


def _speed_score(t_ref: float | None, t: float | None) -> float:
    if t is None or t_ref is None:
        return 0.0
    return float(np.clip(50.0 + (t_ref / t - 1.0) * 500.0, 0.0, 100.0))


@functools.lru_cache(maxsize=16)
def _reference(regs_json: str, cfg: RatingConfig) -> dict:
    import json
    regs = Regulations.from_dict(json.loads(regs_json))
    return _run_battery("pro", regs, cfg)


def _run_battery(pilot: Pilot, regs: Regulations, cfg: RatingConfig) -> dict:
    out: dict = {"speed": [], "rain": [], "tyres": None, "traffic": None}
    for k in cfg.speed_tracks:
        out["speed"].append(run_solo(pilot, validation_track(k), regs, laps=cfg.speed_laps, compound="medium").to_dict())
    for k in cfg.rain_tracks:
        out["rain"].append(run_solo(pilot, validation_track(k), regs, laps=cfg.rain_laps, compound="wet",
                                    weather=wet_weather()).to_dict())
    out["tyres"] = run_solo(pilot, validation_track(cfg.tyre_track), regs, laps=cfg.tyre_laps, compound="soft").to_dict()
    out["traffic"] = run_traffic(pilot, validation_track(cfg.traffic_track), regs, laps=cfg.traffic_laps)
    return out


def _slope(lap_times: list[float]) -> float | None:
    if len(lap_times) < 3:
        return None
    x = np.arange(len(lap_times) - 1)
    y = np.array(lap_times[1:])
    return float(np.polyfit(x, y, 1)[0] / np.mean(y))


def rate(pilot: Pilot, regs: Regulations | None = None, cfg: RatingConfig | None = None) -> Rating:
    import json
    regs = regs or Regulations()
    cfg = cfg or RatingConfig()
    ref = _reference(json.dumps(regs.to_dict(), sort_keys=True), cfg)
    me = _run_battery(pilot, regs, cfg)
    # speed (per circuit)
    speed = [_speed_score(r["best"], m["best"]) for r, m in zip(ref["speed"], me["speed"])]
    # consistency: coefficient of variation of the laps after the first
    cons = []
    for m in me["speed"] + [me["tyres"]]:
        lt = m["lap_times"][1:] if len(m["lap_times"]) > 2 else m["lap_times"]
        if m["crashed"] or len(lt) < 2:
            cons.append(0.0)
        else:
            cv = float(np.std(lt) / np.mean(lt))
            cons.append(float(np.clip(100.0 - 2000.0 * cv, 0.0, 100.0)))
    runs = me["speed"] + me["rain"] + [me["tyres"]]
    survival = 100.0 * (sum(0 if r["crashed"] else 1 for r in runs) + (1 if me["traffic"]["status"] == "finished" else 0)) / (len(runs) + 1)
    rain = [_speed_score(r["best"], m["best"]) for r, m in zip(ref["rain"], me["rain"])]
    # tyres: degradation slope vs reference, finishing the stint matters most
    if me["tyres"]["crashed"]:
        tyres = 0.0
    else:
        s_ref, s_me = _slope(ref["tyres"]["lap_times"]), _slope(me["tyres"]["lap_times"])
        if s_ref is None or s_me is None:
            tyres = 50.0
        else:
            tyres = float(np.clip(50.0 + (s_ref - s_me) / max(abs(s_ref), 1e-4) * 40.0, 0.0, 100.0))
        tyres = 0.7 * tyres + 0.3 * float(np.clip(100.0 * (1.0 - me["tyres"]["wear"]), 0.0, 100.0))
    tr = me["traffic"]
    traffic = 0.0
    if tr["status"] == "finished":
        traffic = 30.0 + 70.0 * tr["gained"] / max(tr["start"] - 1, 1)
    traffic = float(np.clip(traffic - 25.0 * tr["at_fault"], 0.0, 100.0))
    comps = {"speed": float(np.mean(speed)), "consistency": float(np.mean(cons)), "survival": survival,
             "rain": float(np.mean(rain)), "tyres": tyres, "traffic": traffic}
    overall = sum(WEIGHTS[k] * v for k, v in comps.items())
    spread = np.std(speed) if len(speed) > 1 else 6.0
    half = float(1.96 * np.sqrt((WEIGHTS["speed"] * spread) ** 2 + 4.0))
    return Rating(overall, (max(overall - half, 0.0), min(overall + half, 100.0)), comps,
                  {"pilot": me, "reference": ref})
