#!/usr/bin/env python
"""Compare pilots on unseen validation tracks (solo, rolling start, timed laps).

Usage::

    python scripts/compare_pilots.py pro demo_brains/demo-bc demo_brains/demo-ppo --tracks 20 --laps 2

A string that is a reference personality is run as that scripted driver, anything else is loaded as a
brain directory. For every track the best lap of each pilot is divided by the best lap of the first
pilot (the reference); a crash counts as a failure and is excluded from the ratios.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from raicing.brains.spec import Brain
from raicing.config import Regulations
from raicing.drivers.reference import PERSONALITIES
from raicing.lab.evaluation import run_solo, validation_track


def _run(args: tuple[str, int, int]) -> dict:
    name, k, laps = args
    pilot = name if name in PERSONALITIES else Brain.load(Path(name))
    res = run_solo(pilot, validation_track(k), Regulations(), laps=laps)
    ok = len(res.lap_times) >= laps and not res.crashed
    return {"pilot": name, "track": k, "ok": ok, "reason": res.reason,
            "best": float(res.best) if res.best is not None else None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pilots", nargs="+", help="first one is the reference")
    ap.add_argument("--tracks", type=int, default=20)
    ap.add_argument("--laps", type=int, default=2)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    jobs = [(p, k, args.laps) for p in args.pilots for k in range(args.tracks)]
    with ProcessPoolExecutor(args.workers) as ex:
        rows = list(ex.map(_run, jobs))
    by = {(r["pilot"], r["track"]): r for r in rows}
    ref = args.pilots[0]
    summary = {}
    print(f"| pilot | completed | median ratio vs {ref} | best ratio | worst ratio | faster on |")
    print("|---|---|---|---|---|---|")
    for p in args.pilots:
        ratios = []
        for k in range(args.tracks):
            a, b = by[(p, k)], by[(ref, k)]
            if a["ok"] and b["ok"]:
                ratios.append(a["best"] / b["best"])
        done = sum(by[(p, k)]["ok"] for k in range(args.tracks))
        r = np.array(ratios) if ratios else np.array([np.nan])
        summary[p] = {"completed": done, "tracks": args.tracks, "median_ratio": float(np.median(r)),
                      "faster_on": int((r < 1).sum()), "compared": len(ratios)}
        print(f"| {p} | {done}/{args.tracks} | {np.median(r):.3f} | {r.min():.3f} | {r.max():.3f} "
              f"| {(r < 1).sum()}/{len(ratios)} |")
    if args.out:
        args.out.write_text(json.dumps({"laps": args.laps, "summary": summary, "rows": rows}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
