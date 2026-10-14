"""Observation normalisation shared by every lab method.

Statistics are running means/variances of the raw observation (``sensors.observe``). They are
exported into the brain's ``obs_norm`` as ``mean`` and ``std`` with two safety rules:

* ``std`` is floored at ``STD_FLOOR`` (no input is amplified more than 1/STD_FLOOR times);
* inputs that never varied during training (variance below ``DEAD_VAR``: the opponent block in
  solo training, the radio channel without a teammate...) get ``std = MASK_STD``, i.e. the brain
  ignores them. Training never exercised the weights attached to those inputs, so masking them
  changes nothing on the training distribution but keeps the pilot from reacting to random
  untrained weights when the input comes alive in a race.

The raw variance and the sample count are stored too (``obs_norm.var`` / ``obs_norm.count``) so
that a later generation can resume the statistics exactly.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from stable_baselines3.common.running_mean_std import RunningMeanStd
from stable_baselines3.common.vec_env import VecEnv, VecNormalize

from ..brains.spec import Brain
from ..sensors import OBS_DIM

STD_FLOOR = 0.05
DEAD_VAR = 1e-6
MASK_STD = 1e6
CLIP = 10.0


def export_std(var: np.ndarray) -> np.ndarray:
    """Standard deviation used at inference for a running variance (floored, dead inputs masked)."""
    var = np.asarray(var, dtype=np.float64)
    std = np.maximum(np.sqrt(np.maximum(var, 0.0)), STD_FLOOR)
    return np.where(var < DEAD_VAR, MASK_STD, std).astype(np.float32)


def normalize(obs: np.ndarray, mean: np.ndarray, std: np.ndarray, clip: float = CLIP) -> np.ndarray:
    """Exactly the transform ``Brain.forward`` applies (float32)."""
    x = np.asarray(obs, dtype=np.float32)
    return np.clip((x - mean.astype(np.float32)) / (std.astype(np.float32) + np.float32(1e-8)), -clip, clip)


class ObsStats:
    """Running mean / variance (parallel Welford update) of raw observations."""

    def __init__(self, dim: int = OBS_DIM):
        self.rms = RunningMeanStd(shape=(dim,))
        self.rms.count = 0.0
        self.rms.var[:] = 0.0

    @property
    def mean(self) -> np.ndarray:
        return self.rms.mean

    @property
    def var(self) -> np.ndarray:
        return self.rms.var

    @property
    def count(self) -> float:
        return float(self.rms.count)

    def update(self, obs: np.ndarray) -> None:
        obs = np.asarray(obs, dtype=np.float64).reshape(-1, self.rms.mean.shape[0])
        if len(obs) == 0:
            return
        if self.rms.count <= 0:
            self.rms.mean = obs.mean(axis=0)
            self.rms.var = obs.var(axis=0)
            self.rms.count = float(len(obs))
        else:
            self.rms.update(obs)

    def std(self) -> np.ndarray:
        return export_std(self.rms.var)

    def normalize(self, obs: np.ndarray, clip: float = CLIP) -> np.ndarray:
        return normalize(obs, self.rms.mean.astype(np.float32), self.std(), clip)

    def load(self, mean: np.ndarray, var: np.ndarray, count: float) -> None:
        self.rms.mean = np.asarray(mean, dtype=np.float64).copy()
        self.rms.var = np.asarray(var, dtype=np.float64).copy()
        self.rms.count = float(count)


def obs_norm_weights(mean: np.ndarray, var: np.ndarray, count: float, clip: float = CLIP
                     ) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """``obs_norm`` spec entry and weight arrays for a brain."""
    weights = {"obs.mean": np.asarray(mean, dtype=np.float32), "obs.std": export_std(var),
               "obs.var": np.asarray(var, dtype=np.float32)}
    spec = {"mean": "obs.mean", "std": "obs.std", "var": "obs.var", "clip": float(clip), "count": float(count)}
    return spec, weights


def stats_from_brain(brain: Brain) -> tuple[np.ndarray, np.ndarray, float] | None:
    """Raw (mean, var, count) stored in a brain, or ``None`` if it has no normalisation."""
    norm = brain.spec.get("obs_norm")
    if not norm:
        return None
    mean = np.asarray(brain.weights[norm["mean"]], dtype=np.float64)
    if norm.get("var") and norm["var"] in brain.weights:
        var = np.asarray(brain.weights[norm["var"]], dtype=np.float64)
    else:
        var = np.asarray(brain.weights[norm["std"]], dtype=np.float64) ** 2
    return mean, var, float(norm.get("count", 1e6))


class FreezableRunningMeanStd(RunningMeanStd):
    """``RunningMeanStd`` whose updates can be suspended (frozen observation statistics)."""

    frozen: bool = False

    def update(self, arr: np.ndarray) -> None:
        if not self.frozen:
            super().update(arr)


class LabVecNormalize(VecNormalize):
    """``VecNormalize`` whose observation transform is exactly the exported brain's ``obs_norm``.

    ``freeze_obs=True`` keeps the observation statistics fixed (fine-tuning a brain) while the reward
    statistics keep updating. Rewards are scaled by the running return std but barely clipped."""

    def __init__(self, venv: VecEnv, freeze_obs: bool = False, norm_reward: bool = True, gamma: float = 0.99,
                 clip_reward: float = 100.0):
        super().__init__(venv, training=True, norm_obs=True, norm_reward=norm_reward, clip_obs=CLIP,
                         clip_reward=clip_reward, gamma=gamma)
        # start from "no data" rather than SB3's (mean 0, var 1) prior
        self.obs_rms = FreezableRunningMeanStd(shape=self.observation_space.shape)
        self.obs_rms.var[:] = 0.0
        self.obs_rms.count = 1e-4
        self.obs_rms.frozen = bool(freeze_obs)

    def _normalize_obs(self, obs: np.ndarray, obs_rms: RunningMeanStd) -> np.ndarray:  # type: ignore[override]
        return normalize(obs, obs_rms.mean.astype(np.float32), export_std(obs_rms.var), self.clip_obs)

    def load_stats(self, mean: np.ndarray, var: np.ndarray, count: float) -> None:
        self.obs_rms.mean = np.asarray(mean, dtype=np.float64).copy()
        self.obs_rms.var = np.asarray(var, dtype=np.float64).copy()
        self.obs_rms.count = float(count)

    def stats(self) -> tuple[np.ndarray, np.ndarray, float]:
        return self.obs_rms.mean.copy(), self.obs_rms.var.copy(), float(self.obs_rms.count)
