"""Regulation changes between seasons, validated by a bench of historical pilots."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from ..config import Regulations
from ..lab.evaluation import Pilot, run_solo, validation_track


@dataclass(frozen=True)
class ChangeSpec:
    path: str
    label: str
    affects: str | None = None      # garage part that becomes non-compliant
    lo: float = -0.10               # relative range
    hi: float = 0.10


CATALOG: list[ChangeSpec] = [
    ChangeSpec("car.power", "Puissance moteur de référence", "engine", -0.10, 0.06),
    ChangeSpec("car.cla", "Appui aérodynamique de référence", "aero", -0.12, 0.06),
    ChangeSpec("car.cda", "Traînée de référence", "bodywork", -0.06, 0.10),
    ChangeSpec("car.mass", "Masse minimale", "chassis", -0.04, 0.06),
    ChangeSpec("car.max_brake_force", "Force de freinage maximale", "brakes", -0.10, 0.05),
    ChangeSpec("car.tyre_cool_speed", "Refroidissement des pneus", "cooling", -0.12, 0.08),
    ChangeSpec("car.mu_peak", "Adhérence des pneumatiques", "suspension", -0.05, 0.03),
    ChangeSpec("compounds.soft.wear_rate", "Usure des pneus tendres", None, -0.10, 0.12),
    ChangeSpec("compounds.medium.wear_rate", "Usure des pneus medium", None, -0.10, 0.12),
    ChangeSpec("compounds.hard.wear_rate", "Usure des pneus durs", None, -0.10, 0.12),
    ChangeSpec("compounds.soft.grip", "Adhérence des pneus tendres", None, -0.02, 0.02),
    ChangeSpec("race.pit_lane_time", "Temps de passage aux stands", None, -0.15, 0.15),
    ChangeSpec("aero.slipstream_drag_cut", "Effet d'aspiration", None, -0.15, 0.15),
    ChangeSpec("aero.dirty_air_downforce_cut", "Perte d'appui dans l'air perturbé", None, -0.15, 0.15),
    ChangeSpec("weather.rain_probability", "Probabilité de pluie", None, -0.15, 0.15),
]


@dataclass
class RegulationChange:
    season: int
    path: str
    label: str
    old: float
    new: float
    percent: float
    affects: str | None
    rationale: str = ""
    bench: dict[str, Any] = field(default_factory=dict)
    accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def propose(regs: Regulations, season: int, seed: int, exclude: set[str] | None = None) -> RegulationChange:
    """Pick one parameter and a 5–10 % change (rarely up to 15 %)."""
    rng = np.random.default_rng((seed * 1_000_003 + season * 7_919) & 0xFFFFFFFF)
    options = [c for c in CATALOG if c.path not in (exclude or set())]
    spec = options[int(rng.integers(0, len(options)))]
    major = rng.random() < 0.15
    mag = rng.uniform(0.10, 0.15) if major else rng.uniform(0.05, 0.10)
    sign = 1.0 if rng.random() < spec.hi / (spec.hi - spec.lo) else -1.0
    pct = float(np.clip(sign * mag, spec.lo if not major else -0.15, spec.hi if not major else 0.15))
    if abs(pct) < 0.05:
        pct = 0.05 * np.sign(pct or 1.0)
    old = float(regs.get(spec.path))
    new = round(old * (1.0 + pct), 6)
    direction = "hausse" if pct > 0 else "baisse"
    rationale = f"{spec.label} : {direction} de {abs(pct) * 100:.1f} % pour renouveler les compromis techniques."
    return RegulationChange(season=season, path=spec.path, label=spec.label, old=old, new=new,
                            percent=round(pct * 100, 2), affects=spec.affects, rationale=rationale)


def apply(regs: Regulations, change: RegulationChange) -> Regulations:
    out = regs.copy()
    out.set(change.path, change.new)
    out.version = f"S{change.season}"
    return out


def bench(old: Regulations, new: Regulations, pilots: dict[str, Pilot], tracks: tuple[int, ...] = (0, 1),
          laps: int = 2, max_loss: float = 0.05, max_new_crashes: int = 1) -> dict[str, Any]:
    """Compare each bench pilot under the old and new regulations on validation circuits."""
    rows = []
    for name, pilot in pilots.items():
        for k in tracks:
            tr = validation_track(k)
            a = run_solo(pilot, tr, old, laps=laps)
            b = run_solo(pilot, tr, new, laps=laps)
            loss = None
            if a.best and b.best:
                loss = (b.best - a.best) / a.best
            rows.append({"pilot": name, "track": k, "old_best": a.best, "new_best": b.best,
                         "old_crash": a.crashed, "new_crash": b.crashed,
                         "loss": None if loss is None else round(loss, 4)})
    worst = max((r["loss"] for r in rows if r["loss"] is not None), default=0.0)
    new_crashes = sum(1 for r in rows if r["new_crash"] and not r["old_crash"])
    accepted = worst <= max_loss and new_crashes <= max_new_crashes
    return {"rows": rows, "worst_loss": worst, "new_crashes": new_crashes, "accepted": accepted,
            "thresholds": {"max_loss": max_loss, "max_new_crashes": max_new_crashes}}


def choose_change(regs: Regulations, season: int, seed: int, pilots: dict[str, Pilot],
                  attempts: int = 6, run_bench: bool = True) -> RegulationChange:
    """Propose changes until one passes the historical bench (or return the mildest one)."""
    tried: set[str] = set()
    best: RegulationChange | None = None
    for k in range(attempts):
        ch = propose(regs, season, seed + 101 * k, exclude=tried)
        tried.add(ch.path)
        if not run_bench:
            ch.accepted = True
            return ch
        ch.bench = bench(regs, apply(regs, ch), pilots)
        ch.accepted = ch.bench["accepted"]
        if ch.accepted:
            return ch
        if best is None or ch.bench["worst_loss"] < best.bench["worst_loss"]:
            best = ch
    assert best is not None
    best.rationale += " (meilleur compromis disponible au banc historique)"
    best.accepted = True
    return best
