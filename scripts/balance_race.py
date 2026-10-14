#!/usr/bin/env python
"""Balancing bench: what does a full race look like?

16 reference drivers (mixed personalities, identical cars) run qualifying then the race on
validation tracks, dry. Reports retirements (and how many in the first lap), the rank correlation
between grid and finish (1.0 = the qualifying order decides everything), and positions gained.

Usage::

    python scripts/balance_race.py --tracks 6 --workers 4 --out balance/race.json
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from raicing.config import Regulations
from raicing.lab.evaluation import validation_track
from raicing.physics.car import Setup
from raicing.race import Entry, race_laps, run_qualifying, run_race
from raicing.weather import WeatherScript

PERS = ["pro", "rapide", "pneus", "cooperatif", "prudent", "debutant", "pro", "rapide",
        "pneus", "cooperatif", "prudent", "debutant", "pro", "rapide", "pneus", "cooperatif"]


def spearman(a: list[float], b: list[float]) -> float:
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def one(k: int) -> dict:
    regs = Regulations()
    tr = validation_track(k)
    entries = [Entry(entry_id=f"c{i}", team_id=f"t{i // 2}", team_name=f"T{i // 2}", driver_id=f"d{i}",
                     driver_name=f"D{i}", reference=p, setup=Setup(start_compound="medium"))
               for i, p in enumerate(PERS)]
    weather = WeatherScript.dry(22.0)
    q = run_qualifying(tr, entries, regs, weather, seed=k)
    grid = [r["car"] for r in q["classification"]] if "classification" in q else q["grid"]
    laps = race_laps(tr, regs)
    r = run_race(tr, entries, grid, laps, regs, weather, seed=k, record=False)
    rows = r["classification"]
    out = [x for x in rows if x["status"] != "finished"]
    lap1 = [x for x in out if x["laps"] <= 1]
    fin = [x for x in rows if x["status"] == "finished"]
    grid_pos = {car: i + 1 for i, car in enumerate(grid)}
    rho = spearman([grid_pos[x["car"]] for x in fin], [x["pos"] for x in fin]) if len(fin) > 2 else float("nan")
    moves = sum(abs(grid_pos[x["car"]] - x["pos"]) for x in fin)
    return {"track": k, "laps": laps, "finished": len(fin), "out": len(out), "out_lap1": len(lap1),
            "reasons": [x["reason"] for x in out], "rho_grid_finish": rho, "positions_moved": moves,
            "winner_from": grid_pos[rows[0]["car"]], "pits": [x.get("pits") for x in rows],
            "penalties": sum(1 for x in rows if x.get("penalty", 0) > 0)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tracks", type=int, default=6)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    with ProcessPoolExecutor(a.workers) as ex:
        res = list(ex.map(one, range(a.tracks)))
    print("| track | laps | finished | out (lap 1) | grid→finish corr. | winner from | penalties |")
    print("|---|---|---|---|---|---|---|")
    for r in res:
        print(f"| val:{r['track']} | {r['laps']} | {r['finished']}/16 | {r['out']} ({r['out_lap1']}) | "
              f"{r['rho_grid_finish']:.2f} | P{r['winner_from']} | {r['penalties']} |")
    if a.out:
        a.out.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
