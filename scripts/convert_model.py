#!/usr/bin/env python
"""Convert a pilot trained elsewhere into a brain package (.zip) for the "Rencontres" page.

Supported inputs:

* stable-baselines3 PPO / SAC / TD3 saved with ``model.save("pilot.zip")`` (MlpPolicy, Box actions);
  ``--vecnormalize vec_normalize.pkl`` embeds the observation statistics of ``VecNormalize``;
* a PyTorch ``nn.Sequential`` MLP saved whole with ``torch.save(net, "pilot.pt")``
  (``--squash tanh|clip|none`` says how the last layer's output is mapped to [-1, 1]).

The network must read the 157-value observation of the simulator and output 2 to 11 actions
(see docs/RENCONTRES.md). The result is checked by running it on a real observation.

Usage::

    python scripts/convert_model.py ppo pilot.zip -o mon-pilote.zip [--vecnormalize vec_normalize.pkl]
    python scripts/convert_model.py torch pilot.pt -o mon-pilote.zip --squash tanh
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np


def obs_stats_from_vecnormalize(path: Path):
    with open(path, "rb") as f:
        vn = pickle.load(f)  # local file chosen by the user
    rms = vn.obs_rms
    return np.asarray(rms.mean, dtype=np.float64), np.asarray(rms.var, dtype=np.float64), float(rms.count)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["ppo", "sac", "td3", "torch"])
    ap.add_argument("model", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True, help="paquet de sortie (.zip)")
    ap.add_argument("--vecnormalize", type=Path, help="statistiques VecNormalize (stable-baselines3)")
    ap.add_argument("--squash", default="tanh", choices=["tanh", "clip", "none"], help="sortie d'un modèle torch")
    ap.add_argument("--name", default=None, help="nom enregistré dans le modèle")
    a = ap.parse_args()

    import torch

    from raicing.brains.spec import Brain
    from raicing.lab.export import export_ppo, export_sac, export_sequential, export_td3
    from raicing.sensors import OBS_DIM

    stats = obs_stats_from_vecnormalize(a.vecnormalize) if a.vecnormalize else None
    meta = {"method": a.kind, "source": a.model.name}
    if a.name:
        meta["driver_id"] = a.name
    if a.kind in ("ppo", "sac", "td3"):
        from stable_baselines3 import PPO, SAC, TD3
        cls = {"ppo": PPO, "sac": SAC, "td3": TD3}[a.kind]
        model = cls.load(str(a.model), device="cpu")
        shape = model.observation_space.shape
        if shape != (OBS_DIM,):
            print(f"Erreur : le modèle lit {shape} valeurs, le simulateur en fournit ({OBS_DIM},)", file=sys.stderr)
            return 1
        brain = {"ppo": export_ppo, "sac": export_sac, "td3": export_td3}[a.kind](model, stats, meta)
    else:
        net = torch.load(str(a.model), map_location="cpu", weights_only=False)
        brain = export_sequential(list(net) if isinstance(net, torch.nn.Sequential) else [net], a.squash, stats, meta)

    # sanity check: one decision on a neutral observation
    act, _ = brain.act(np.zeros((1, OBS_DIM), dtype=np.float32), brain.init_state(1))
    if not np.all(np.isfinite(act)):
        print("Erreur : le modèle produit des valeurs non finies", file=sys.stderr)
        return 1
    a.out.write_bytes(brain.to_bytes())
    out_dim = int(brain.spec["output"]["dim"])
    params = sum(int(w.size) for w in brain.weights.values())
    print(f"OK : {a.out} ({params} paramètres, {out_dim} sorties"
          f"{', arrêts aux stands possibles' if out_dim >= 7 else ', pas d’arrêt aux stands'})")
    Brain.from_bytes(a.out.read_bytes()).validate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
