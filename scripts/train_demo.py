#!/usr/bin/env python
"""Train the demonstration pilot of the lab and report how it drives.

Recipe (cheapest effective one found on a 2-core CPU, see docs/LAB.md):

1. ``bc``  — behaviour cloning of the reference "pro" driver with DAgger (8 rounds; the learner
   drives alone from round 5 on and the teacher only labels). ~1.6 M interactions, ~10 min.
2. ``ppo`` — PPO fine-tuning of that brain in solo mode (small exploration noise, frozen observation
   statistics, critic warm-up). Optional (``--ppo-steps 0`` skips it).

Training only uses lab tracks 5..1004 (``curriculum.tracks``): lab tracks 0..4 and validation tracks
0..4 are never seen and serve as the evaluation. Each brain is evaluated with ``run_solo`` (2 timed
laps after a rolling start) and compared with ``run_solo("pro", ...)``.

Usage::

    python scripts/train_demo.py                       # full recipe
    python scripts/train_demo.py --bc-steps 1600000 --ppo-steps 4000000 --name demo
    python scripts/train_demo.py --skip-bc --ppo-steps 3000000   # fine-tune an existing demo-bc brain
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch

from raicing.brains.spec import Brain
from raicing.config import Regulations
from raicing.lab.evaluation import lab_track, run_solo, validation_track
from raicing.lab.jobs import jsonable, run_job

ROOT = Path(__file__).resolve().parents[1]
TRAIN_TRACKS = list(range(5, 1005))          # lab tracks 0..4 are held out for evaluation


def evaluate(pilot, regs: Regulations, laps: int = 2) -> list[dict]:
    rows = []
    for kind, fn in (("lab", lab_track), ("val", validation_track)):
        for k in range(5):
            tr = fn(k)
            res = run_solo(pilot, tr, regs, laps=laps)
            rows.append({"track": f"{kind}:{k}", "name": tr.name, "length": round(tr.length, 1),
                         "laps": len(res.lap_times), "crashed": bool(res.crashed), "reason": res.reason,
                         "lap_times": [round(float(x), 3) for x in res.lap_times],
                         "best": round(float(res.best), 3) if res.best is not None else None})
    return rows


def table(rows: list[dict], ref: dict[str, dict], laps: int = 2) -> str:
    lines = ["| track | laps | best lap (s) | pro best (s) | ratio | status |", "|---|---|---|---|---|---|"]
    for r in rows:
        p = ref[r["track"]]["best"]
        ratio = f"{r['best'] / p:.3f}" if (r["best"] and p) else "—"
        status = "ok" if r["laps"] >= laps and not r["crashed"] else (r["reason"] or "crash")
        lines.append(f"| {r['track']} {r['name']} | {r['laps']}/{laps} | {r['best'] or '—'} | {p} | {ratio} | {status} |")
    ok_lab = sum(1 for r in rows if r["track"].startswith("lab") and r["laps"] >= laps and not r["crashed"])
    ok_val = sum(1 for r in rows if r["track"].startswith("val") and r["laps"] >= laps and not r["crashed"])
    lines.append(f"\nCompleted {laps} laps: lab {ok_lab}/5, validation {ok_val}/5")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="demo")
    ap.add_argument("--bc-steps", type=int, default=1_600_000)
    ap.add_argument("--ppo-steps", type=int, default=4_000_000)
    ap.add_argument("--skip-bc", action="store_true", help="reuse demo_brains/<name>-bc")
    ap.add_argument("--envs", type=int, default=64)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--ppo-lr", type=float, default=5e-5)
    ap.add_argument("--ppo-log-std", type=float, default=-2.0)
    ap.add_argument("--hidden", default="256,256")
    args = ap.parse_args()
    torch.set_num_threads(2)
    regs = Regulations()
    out = ROOT / "demo_brains"
    hidden = [int(h) for h in args.hidden.split(",")]
    arch = {"hidden": hidden, "activation": "tanh", "outputs": 2, "squash": "clip"}
    curriculum = {"mode": "solo", "rain_fraction": 0.0, "tyre_randomization": True, "track_rotation": 2000,
                  "tracks": TRAIN_TRACKS, "episode_decisions": 1500}
    t0 = time.time()
    print("reference 'pro' driver ...", flush=True)
    ref_rows = evaluate("pro", regs)
    ref = {r["track"]: r for r in ref_rows}
    report: dict = {"reference_pro": ref_rows, "stages": []}

    def progress(d: dict) -> None:
        print("  progress", {k: v for k, v in d.items() if k in ("steps", "mean_reward", "crash_rate", "loss", "beta", "time")},
              flush=True)

    bc_dir = out / f"{args.name}-bc"
    if args.skip_bc:
        brain = Brain.load(bc_dir)
        if (bc_dir / "eval.json").exists():
            rows = json.loads((bc_dir / "eval.json").read_text())
            report["stages"].append({"name": f"{args.name}-bc", "method": "bc", "steps": brain.meta.get("steps"),
                                     "eval": rows, "reused": True})
    else:
        cfg = {"driver_id": f"{args.name}-bc", "method": "bc", "steps": args.bc_steps, "envs": args.envs,
               "seed": args.seed, "architecture": arch,
               "hyperparams": {"teacher": "pro", "iterations": 8, "epochs": 4},
               "curriculum": curriculum}
        print(f"[stage 1] bc {args.bc_steps} steps", flush=True)
        res = run_job(cfg, regs, None, args.bc_steps, bc_dir, progress=progress, verbose=True)
        brain = res.brain
        rows = evaluate(brain, regs)
        print(table(rows, ref), flush=True)
        report["stages"].append({"name": f"{args.name}-bc", "method": "bc", "steps": res.steps_used,
                                 "duration_s": res.log["duration"], "throughput": res.log["throughput"], "eval": rows})
        (bc_dir / "eval.json").write_text(json.dumps(jsonable(rows), indent=1, ensure_ascii=False))
    if args.ppo_steps > 0:
        ppo_dir = out / f"{args.name}-ppo"
        cfg = {"driver_id": f"{args.name}-ppo", "method": "ppo", "steps": args.ppo_steps, "envs": args.envs,
               "seed": args.seed, "init_from": f"{args.name}-bc", "architecture": arch,
               "hyperparams": {"lr": args.ppo_lr, "log_std_init": args.ppo_log_std, "critic_warmup": 4,
                               "n_steps": 256, "batch_size": 4096, "n_epochs": 5, "gamma": 0.99},
               "curriculum": curriculum}
        print(f"[stage 2] ppo fine-tuning {args.ppo_steps} steps", flush=True)
        res = run_job(cfg, regs, brain, args.ppo_steps, ppo_dir, progress=progress, verbose=True)
        rows = evaluate(res.brain, regs)
        print(table(rows, ref), flush=True)
        report["stages"].append({"name": f"{args.name}-ppo", "method": "ppo", "steps": res.steps_used,
                                 "duration_s": res.log["duration"], "throughput": res.log["throughput"], "eval": rows})
        (ppo_dir / "eval.json").write_text(json.dumps(jsonable(rows), indent=1, ensure_ascii=False))
    report["total_time_s"] = round(time.time() - t0, 1)
    (out / f"{args.name}-report.json").write_text(json.dumps(jsonable(report), indent=1, ensure_ascii=False))
    print(f"total {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
