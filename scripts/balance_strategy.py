#!/usr/bin/env python
"""Balancing bench: are several pit strategies viable?

The reference driver "pro" runs alone (no traffic) a full race on validation tracks, dry weather,
following an imposed plan: start compound, then a stop at the end of given laps with given
compounds. For each track the best plan of each family (0, 1 and 2 stops) is compared.

Usage::

    python scripts/balance_strategy.py --tracks 6 --workers 4 --out balance-strategy.json
    python scripts/balance_strategy.py --set race.pit_lane_time=12   # try a regulation tweak
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from raicing.config import COMPOUND_INDEX, Regulations
from raicing.drivers.reference import ReferenceDriver
from raicing.lab.evaluation import validation_track
from raicing.physics.car import build_spec
from raicing.race import estimate_lap_time, race_laps
from raicing.weather import WeatherScript
from raicing.world import FINISHED, SessionConfig, World


class PlannedDriver(ReferenceDriver):
    """Reference driving with an imposed pit plan: ``stops`` = [(lap, compound), ...]."""

    def __init__(self, world: World, stops: list[tuple[int, str]], laps: int):
        super().__init__(world, [0], "pro", laps=laps)
        self.stops = sorted(stops)

    def _strategy(self, i, p, wear, wet, comp):
        done = int(np.floor(self.w.progress[i] / self.w.track.length))  # completed laps (-1 on the grid)
        k = int(self.w.pit_stops[i])
        if k < len(self.stops):
            lap, c = self.stops[k]
            if done + 1 >= lap:  # on the planned lap: box at its end
                return True, COMPOUND_INDEX[c]
        return False, comp


def apply_overrides(regs: Regulations, sets: list[str]) -> Regulations:
    for s in sets:
        path, value = s.split("=", 1)
        regs.set(path, float(value)) if hasattr(regs, "set") else _set_path(regs, path, float(value))
    return regs


def _set_path(obj, path: str, value: float) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        obj = obj[p] if isinstance(obj, dict) else getattr(obj, p)
    if isinstance(obj, dict):
        obj[parts[-1]] = value
    else:
        setattr(obj, parts[-1], type(getattr(obj, parts[-1]))(value))


def run_plan(args: tuple) -> dict:
    k, start, stops, sets = args
    regs = apply_overrides(Regulations(), sets)
    tr = validation_track(k)
    laps = race_laps(tr, regs)
    lap_est = estimate_lap_time(tr, regs)
    sess = SessionConfig(kind="race", laps=laps, interactions=False, record=False,
                         max_time=regs.race.max_time_factor * laps * lap_est)
    w = World(tr, [build_spec(regs)], regs, WeatherScript.dry(22.0), sess, seed=1)
    w.setup_grid([0], [start])
    drv = PlannedDriver(w, [(int(l), c) for l, c in stops], laps)
    while not w.done():
        w.step(drv.act())
    ok = int(w.status[0]) == FINISHED and not np.isnan(w.finish_time[0])
    return {"track": k, "laps": laps, "start": start, "stops": stops, "n_stops": int(w.pit_stops[0]),
            "ok": ok, "reason": w.reason[0], "time": float(w.finish_time[0]) if ok else None,
            "wear_end": round(float(max(w.wear_f[0], w.wear_r[0])), 3)}


def plans(laps: int) -> list[tuple[str, list[tuple[int, str]]]]:
    out: list[tuple[str, list[tuple[int, str]]]] = [(c, []) for c in ("soft", "medium", "hard")]
    for f in (0.3, 0.4, 0.5, 0.6, 0.7):
        lap = max(1, min(laps - 1, round(laps * f)))
        for a, b in (("soft", "hard"), ("soft", "medium"), ("medium", "hard"), ("medium", "medium"),
                     ("hard", "medium"), ("hard", "soft"), ("medium", "soft"), ("soft", "soft")):
            out.append((a, [(lap, b)]))
    for f1, f2 in ((0.25, 0.6), (0.33, 0.67), (0.4, 0.75)):
        l1 = max(1, round(laps * f1))
        l2 = max(l1 + 1, min(laps - 1, round(laps * f2)))
        for a, b, c in (("soft", "soft", "soft"), ("soft", "medium", "soft"), ("medium", "soft", "soft"),
                        ("soft", "soft", "medium"), ("medium", "medium", "soft")):
            out.append((a, [(l1, b), (l2, c)]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tracks", type=int, default=6)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--set", action="append", default=[], help="regulation override path=value")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    regs = apply_overrides(Regulations(), a.set)
    jobs = []
    for k in range(a.tracks):
        laps = race_laps(validation_track(k), regs)
        jobs += [(k, s, st, a.set) for s, st in plans(laps)]
    with ProcessPoolExecutor(a.workers) as ex:
        rows = list(ex.map(run_plan, jobs, chunksize=4))
    summary = []
    print("| track | laps | best 0 stop | best 1 stop | best 2 stops | winner | gap 2nd family |")
    print("|---|---|---|---|---|---|---|")
    for k in range(a.tracks):
        rk = [r for r in rows if r["track"] == k]
        best = {}
        for n in (0, 1, 2):
            fam = [r for r in rk if r["ok"] and r["n_stops"] == n]
            best[n] = min(fam, key=lambda r: r["time"]) if fam else None

        def fmt(r):
            if r is None:
                return "—"
            plan = "→".join([r["start"][0].upper()] + [c[0].upper() for _, c in r["stops"]])
            laps_s = ",".join(str(l) for l, _ in r["stops"])
            return f"{r['time']:.1f} s ({plan}{' @' + laps_s if laps_s else ''})"

        times = sorted((r["time"], n) for n, r in best.items() if r)
        win = times[0][1]
        gap = times[1][0] - times[0][0] if len(times) > 1 else float("nan")
        print(f"| val:{k} | {rk[0]['laps']} | {fmt(best[0])} | {fmt(best[1])} | {fmt(best[2])} | {win} | {gap:+.1f} s |")
        summary.append({"track": k, "laps": rk[0]["laps"], "best": {n: best[n] for n in best}, "winner": win, "gap": gap})
    fails = sum(not r["ok"] for r in rows)
    print(f"\n{len(rows)} plans, {fails} not finished (punctures/crashes)")
    if a.out:
        a.out.write_text(json.dumps({"overrides": a.set, "summary": summary, "rows": rows}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
