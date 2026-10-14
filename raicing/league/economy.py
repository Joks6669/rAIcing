"""Garage economy: parts, technologies, revisions, capacity limits and season budgets.

Pure functions over a ``GarageState`` (a plain dict-friendly dataclass) so that the same rules
are used by the CLI (local league), the API and the tests.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from ..config import EconomyRules, Regulations
from ..physics.car import PerformanceModifiers


@dataclass(frozen=True)
class Part:
    key: str
    label: str
    base_cost: float
    volume: float          # volume used per level
    thermal: float         # thermal load per level
    mass: float            # kg added per level
    effect: str            # human readable effect
    max_gain: float        # asymptotic gain of the main effect


PARTS: dict[str, Part] = {
    "engine": Part("engine", "Moteur", 70, 1.0, 2.0, 2.5, "Puissance", 0.18),
    "aero": Part("aero", "Appui aérodynamique", 65, 1.5, 0.0, 1.0, "Appui (ClA)", 0.22),
    "bodywork": Part("bodywork", "Carrosserie basse traînée", 55, 1.0, 1.0, 0.5, "Traînée (CdA)", 0.12),
    "chassis": Part("chassis", "Châssis", 60, 0.0, 0.0, -4.0, "Masse et capacité d'intégration", 0.0),
    "brakes": Part("brakes", "Freins", 40, 0.5, 1.0, 1.0, "Force de freinage", 0.25),
    "cooling": Part("cooling", "Refroidissement", 45, 1.0, 0.0, 1.5, "Capacité thermique et stabilité thermique des pneus", 0.14),
    "suspension": Part("suspension", "Suspension", 50, 0.5, 0.0, 1.0, "Adhérence mécanique et usure", 0.05),
}


@dataclass(frozen=True)
class Technology:
    key: str
    label: str
    cost: float
    description: str


TECHNOLOGIES: dict[str, Technology] = {
    "composites": Technology("composites", "Matériaux composites", 120,
                             "+3 d'encombrement, châssis 30 % plus léger par niveau"),
    "active_cooling": Technology("active_cooling", "Refroidissement actif", 110, "+4 de capacité thermique"),
    "low_drag": Technology("low_drag", "Aileron basse traînée", 100,
                           "Débloque la carrosserie au-delà du niveau 5, −2 % de traînée"),
    "carbon_brakes": Technology("carbon_brakes", "Freins carbone", 90, "Débloque les freins au-delà du niveau 5"),
    "tyre_telemetry": Technology("tyre_telemetry", "Télémétrie pneumatique", 80, "−5 % d'usure des pneus"),
    "energy_recovery": Technology("energy_recovery", "Récupération d'énergie", 150, "+3 % de puissance, +1 de charge thermique"),
}

# compatibility rules: (part, level threshold, requirement description, predicate)
REQUIREMENTS: list[tuple[str, int, str]] = [
    ("aero", 7, "chassis>=4"),
    ("engine", 7, "cooling>=4"),
    ("bodywork", 6, "tech:low_drag"),
    ("brakes", 6, "tech:carbon_brakes"),
]

BASE_VOLUME = 12.0
BASE_THERMAL = 8.0


@dataclass
class GarageState:
    season: int = 1
    budget: float = 1000.0          # budget granted for this season (incl. carry-over)
    spent: float = 0.0
    parts: dict[str, int] = field(default_factory=lambda: {k: 0 for k in PARTS})
    technologies: list[str] = field(default_factory=list)
    non_compliant: dict[str, int] = field(default_factory=dict)   # part -> capped level until revised
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def available(self) -> float:
        return round(self.budget - self.spent, 2)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["available"] = self.available
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GarageState":
        g = cls()
        for k in ("season", "budget", "spent", "technologies", "non_compliant", "history"):
            if k in d:
                setattr(g, k, d[k])
        g.parts = {k: int(d.get("parts", {}).get(k, 0)) for k in PARTS}
        return g


class GarageError(ValueError):
    pass


# ---------------------------------------------------------------------- rules
def gain(level: float, max_gain: float) -> float:
    """Diminishing returns: most of the gain comes from the first levels."""
    return max_gain * (1.0 - math.exp(-level / 3.5)) / (1.0 - math.exp(-10 / 3.5))


def upgrade_cost(part: str, level: int, rules: EconomyRules | None = None) -> float:
    rules = rules or EconomyRules()
    return round(PARTS[part].base_cost * rules.cost_growth ** level, 1)


def revision_cost(part: str, level: int) -> float:
    return round(PARTS[part].base_cost * 0.6 * max(level, 1), 1)


def effective_levels(g: GarageState) -> dict[str, int]:
    lv = dict(g.parts)
    for p, cap in g.non_compliant.items():
        lv[p] = min(lv[p], int(cap))
    return lv


def capacity(g: GarageState, levels: dict[str, int] | None = None) -> dict[str, Any]:
    lv = levels or g.parts
    vol_cap = BASE_VOLUME + 2.0 * lv["chassis"] + (3.0 if "composites" in g.technologies else 0.0)
    th_cap = BASE_THERMAL + 2.0 * lv["cooling"] + (4.0 if "active_cooling" in g.technologies else 0.0)
    vol = sum(PARTS[p].volume * lv[p] for p in PARTS)
    th = sum(PARTS[p].thermal * lv[p] for p in PARTS) + (1.0 if "energy_recovery" in g.technologies else 0.0)
    chassis_cut = 30.0 * gain(lv["chassis"], 1.0) * (1.3 if "composites" in g.technologies else 1.0)
    mass = sum(PARTS[p].mass * lv[p] for p in PARTS if p != "chassis") - chassis_cut
    return {"volume": [round(vol, 2), round(vol_cap, 2)], "thermal": [round(th, 2), round(th_cap, 2)],
            "mass_delta": round(mass, 2)}


def check_requirements(g: GarageState, levels: dict[str, int]) -> list[str]:
    errors = []
    for part, threshold, req in REQUIREMENTS:
        if levels[part] >= threshold:
            if req.startswith("tech:"):
                if req[5:] not in g.technologies:
                    errors.append(f"{part} niveau {levels[part]} exige la technologie {req[5:]}")
            else:
                other, val = req.split(">=")
                if levels[other] < int(val):
                    errors.append(f"{part} niveau {levels[part]} exige {other} ≥ {val}")
    cap = capacity(g, levels)
    if cap["volume"][0] > cap["volume"][1]:
        errors.append(f"encombrement dépassé ({cap['volume'][0]} > {cap['volume'][1]})")
    if cap["thermal"][0] > cap["thermal"][1]:
        errors.append(f"charge thermique dépassée ({cap['thermal'][0]} > {cap['thermal'][1]})")
    return errors


def catalog(g: GarageState, rules: EconomyRules | None = None) -> dict[str, Any]:
    rules = rules or EconomyRules()
    parts = []
    for k, p in PARTS.items():
        lv = g.parts[k]
        nxt = dict(g.parts)
        nxt[k] = lv + 1
        blockers = check_requirements(g, nxt) if lv < rules.max_level else ["niveau maximal atteint"]
        parts.append({"part": k, "label": p.label, "level": lv, "effect": p.effect,
                      "next_cost": upgrade_cost(k, lv, rules) if lv < rules.max_level else None,
                      "current_gain": round(gain(lv, p.max_gain), 4), "next_gain": round(gain(lv + 1, p.max_gain), 4),
                      "volume_per_level": p.volume, "thermal_per_level": p.thermal, "mass_per_level": p.mass,
                      "blocked_by": blockers,
                      "non_compliant_cap": g.non_compliant.get(k),
                      "revision_cost": revision_cost(k, lv) if k in g.non_compliant else None})
    techs = [{"technology": t.key, "label": t.label, "cost": t.cost, "description": t.description,
              "owned": t.key in g.technologies} for t in TECHNOLOGIES.values()]
    return {"budget": g.budget, "spent": g.spent, "available": g.available, "parts": parts,
            "technologies": techs, "capacity": capacity(g), "requirements": REQUIREMENTS}


# ---------------------------------------------------------------------- actions
def _charge(g: GarageState, cost: float, kind: str, item: str) -> None:
    if cost > g.available + 1e-9:
        raise GarageError(f"budget insuffisant : {cost} points nécessaires, {g.available} disponibles")
    g.spent = round(g.spent + cost, 2)
    g.history.append({"season": g.season, "kind": kind, "item": item, "cost": cost})


def upgrade(g: GarageState, part: str, rules: EconomyRules | None = None) -> float:
    rules = rules or EconomyRules()
    if part not in PARTS:
        raise GarageError(f"pièce inconnue : {part}")
    lv = g.parts[part]
    if lv >= rules.max_level:
        raise GarageError("niveau maximal atteint")
    if part in g.non_compliant:
        raise GarageError("pièce non conforme : révision obligatoire avant amélioration")
    nxt = dict(g.parts)
    nxt[part] = lv + 1
    errs = check_requirements(g, nxt)
    if errs:
        raise GarageError("; ".join(errs))
    cost = upgrade_cost(part, lv, rules)
    _charge(g, cost, "construction", f"{part}:{lv + 1}")
    g.parts[part] = lv + 1
    return cost


def research(g: GarageState, tech: str) -> float:
    if tech not in TECHNOLOGIES:
        raise GarageError(f"technologie inconnue : {tech}")
    if tech in g.technologies:
        raise GarageError("technologie déjà acquise")
    cost = TECHNOLOGIES[tech].cost
    _charge(g, cost, "research", tech)
    g.technologies.append(tech)
    return cost


def revise(g: GarageState, part: str) -> float:
    if part not in g.non_compliant:
        raise GarageError("cette pièce est conforme")
    cost = revision_cost(part, g.parts[part])
    _charge(g, cost, "revision", part)
    del g.non_compliant[part]
    return cost


# ---------------------------------------------------------------------- season & performance
def season_budget(season: int, league_seed: int, rules: EconomyRules | None = None) -> int:
    rules = rules or EconomyRules()
    if season <= 1:
        return rules.first_budget
    rng = np.random.default_rng((league_seed * 7919 + season) & 0xFFFFFFFF)
    return int(round(rng.uniform(rules.budget_min, rules.budget_max) / 10.0) * 10)


def new_season(g: GarageState, season: int, budget: int, rules: EconomyRules | None = None,
               affected_parts: list[str] | None = None) -> GarageState:
    """Roll a garage over to the next season: carry-over and regulation-driven non-compliance."""
    rules = rules or EconomyRules()
    carry = min(max(g.available, 0.0), rules.carry_over_fraction * budget)
    ng = GarageState(season=season, budget=round(budget + carry, 2), spent=0.0, parts=dict(g.parts),
                     technologies=list(g.technologies), non_compliant=dict(g.non_compliant),
                     history=list(g.history))
    for p in affected_parts or []:
        if ng.parts.get(p, 0) > 0:
            ng.non_compliant[p] = int(ng.parts[p] * 0.6)
    return ng


def modifiers(g: GarageState) -> PerformanceModifiers:
    lv = effective_levels(g)
    P = PARTS
    techs = set(g.technologies)
    cap = capacity(g, lv)
    power = 1.0 + gain(lv["engine"], P["engine"].max_gain) + (0.03 if "energy_recovery" in techs else 0.0)
    cla = 1.0 + gain(lv["aero"], P["aero"].max_gain)
    cda = 1.0 - gain(lv["bodywork"], P["bodywork"].max_gain) - (0.02 if "low_drag" in techs else 0.0)
    brake = 1.0 + gain(lv["brakes"], P["brakes"].max_gain)
    cooling = 1.0 + gain(lv["cooling"], P["cooling"].max_gain)
    grip = 1.0 + gain(lv["suspension"], P["suspension"].max_gain)
    wear = (1.0 - 2.4 * gain(lv["suspension"], P["suspension"].max_gain)) * (0.95 if "tyre_telemetry" in techs else 1.0)
    return PerformanceModifiers(power=power, cla=cla, cda=cda, mass_delta=cap["mass_delta"], brake=brake,
                                cooling=cooling, mech_grip=grip, wear=wear, notes={"levels": lv})


def apply_regulations_to_spec_mods(mods: PerformanceModifiers, regs: Regulations) -> PerformanceModifiers:
    """Hook for regulations that cap part effects (kept for forward compatibility)."""
    return mods
