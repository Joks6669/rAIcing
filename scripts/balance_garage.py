#!/usr/bin/env python
"""Balancing bench: what does each garage part buy, per point spent?

The reference driver "pro" runs 2 timed laps alone (dry, medium tyres) on validation tracks with a
car where a single part is raised to a given level (compatibility rules ignored). The gain is the
best-lap time saved versus the base car, and its efficiency the time saved per 100 points spent.

Limits: one-lap pace only (the wear effect of the suspension and the tyre-cooling effect of the
cooling part show over a stint, not here); the reference driver's braking model does not use the
brake force, so the brakes' gain is a lower bound.

Usage::

    python scripts/balance_garage.py --tracks 4 --workers 4 --out balance/garage.json
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from raicing.config import Regulations
from raicing.lab.evaluation import run_solo, validation_track
from raicing.league.economy import PARTS, TECHNOLOGIES, GarageState, modifiers, upgrade_cost
from raicing.physics.car import build_spec

LEVELS = (1, 2, 3, 5, 7)
TECHS = ("energy_recovery", "low_drag", "tyre_telemetry")


def cost_to(part: str, level: int) -> float:
    return sum(upgrade_cost(part, i) for i in range(level))


def lap(args: tuple) -> dict:
    k, part, level, tech = args
    regs = Regulations()
    g = GarageState()
    if part:
        g.parts[part] = level
    if tech:
        g.technologies = [tech]
    spec = build_spec(regs, modifiers(g))
    res = run_solo("pro", validation_track(k), regs, laps=2, spec=spec)
    return {"track": k, "part": part, "level": level, "tech": tech,
            "best": float(res.best) if res.best is not None and not res.crashed else None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tracks", type=int, default=4)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    jobs = [(k, None, 0, None) for k in range(a.tracks)]
    jobs += [(k, p, L, None) for k in range(a.tracks) for p in PARTS for L in LEVELS]
    jobs += [(k, None, 0, t) for k in range(a.tracks) for t in TECHS]
    with ProcessPoolExecutor(a.workers) as ex:
        rows = list(ex.map(lap, jobs, chunksize=2))
    base = {r["track"]: r["best"] for r in rows if r["part"] is None and r["tech"] is None}

    def gain_pct(sel):
        g = [(base[r["track"]] - r["best"]) / base[r["track"]] * 100 for r in sel if r["best"] and base[r["track"]]]
        return float(np.mean(g)) if g else float("nan")

    table = []
    print("| part | level | cost | lap time gain | gain per 100 pts |")
    print("|---|---|---|---|---|")
    for p in PARTS:
        for L in LEVELS:
            sel = [r for r in rows if r["part"] == p and r["level"] == L]
            c = cost_to(p, L)
            gp = gain_pct(sel)
            table.append({"part": p, "level": L, "cost": c, "gain_pct": gp, "per100": gp / c * 100})
            print(f"| {p} | {L} | {c:.0f} | {gp:.2f} % | {gp / c * 100:.3f} % |")
    for t in TECHS:
        sel = [r for r in rows if r["tech"] == t]
        c = TECHNOLOGIES[t].cost
        gp = gain_pct(sel)
        table.append({"tech": t, "cost": c, "gain_pct": gp, "per100": gp / c * 100})
        print(f"| tech:{t} | — | {c:.0f} | {gp:.2f} % | {gp / c * 100:.3f} % |")
    if a.out:
        a.out.write_text(json.dumps({"base": base, "table": table, "rows": rows}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
