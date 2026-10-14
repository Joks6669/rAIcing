#!/usr/bin/env python
"""Measure the lab's training throughput (interactions per second) for every method.

Each method trains a default-sized pilot (64 cars, MLP 256x256 tanh) in solo mode for a short
budget; the reported figure is ``steps_used / wall-clock`` of the whole job (simulation, inference
and learning updates included, evaluation excluded).

    python scripts/bench_lab.py [--scale 1.0]
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time

import torch

from raicing.config import Regulations
from raicing.lab.jobs import run_job

RUNS = [
    ("ppo", 250_000, {}),
    ("sac", 25_000, {"learning_starts": 5_000}),
    ("td3", 25_000, {"learning_starts": 5_000}),
    ("es", 200_000, {"population": 64, "episode_decisions": 1000}),
    ("bc", 200_000, {"iterations": 2}),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0, help="multiply every budget")
    ap.add_argument("--methods", default="ppo,sac,td3,es,bc")
    args = ap.parse_args()
    torch.set_num_threads(2)
    regs = Regulations()
    out = {}
    for method, steps, hp in RUNS:
        if method not in args.methods.split(","):
            continue
        steps = int(steps * args.scale)
        arch = {"hidden": [256, 256], "activation": "tanh", "outputs": 2}
        if method == "es":
            arch = {"hidden": [64, 64], "activation": "tanh", "outputs": 2}
        cfg = {"method": method, "steps": steps, "envs": 64, "architecture": arch, "hyperparams": hp,
               "curriculum": {"rain_fraction": 0.0}}
        with tempfile.TemporaryDirectory() as d:
            t0 = time.time()
            res = run_job(cfg, regs, None, steps, d, evaluate=False)
            dt = time.time() - t0
        out[method] = {"steps": res.steps_used, "seconds": round(dt, 1), "per_second": round(res.steps_used / dt)}
        print(method, out[method], flush=True)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
