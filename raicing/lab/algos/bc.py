"""Behaviour cloning of a reference driver, with DAgger-style data aggregation.

Iteration ``i`` drives every learner car with the mixture "teacher with probability beta_i, learner
otherwise" (decided per car and per decision), labels *every* visited state with the teacher's
action, aggregates the labels and refits the network. ``beta_0 = 1`` (pure demonstrations), then
``beta_i = beta_decay ** i`` until it drops under 0.05, after which the learner drives alone and the
teacher only labels (DAgger). With ``dagger=False`` the teacher always drives (plain BC).

Every labelled decision is one interaction of the quota (a demonstration counts, per docs/LAB.md).
The network is a plain MLP (optional LayerNorm) exported with squash ``clip`` (default, compatible
with PPO fine-tuning) or ``tanh`` (compatible with SAC/TD3 fine-tuning).
"""
from __future__ import annotations

import time
from typing import Any, Callable

import numpy as np
import torch
import torch.nn as nn

from ...brains.spec import Brain
from ...config import Regulations
from ...drivers.reference import PERSONALITIES
from ..envs import LabSim, settings_from_config
from ..export import ACTIVATION_CLASSES, export_sequential, load_into_modules
from ..normalize import CLIP, ObsStats, stats_from_brain
from ..quota import QuotaCounter, QuotaExceeded
from ..teachers import CachedReferenceDriver

NOT_CRASH = ("time_limit", "finished", "track_rotation", "race_over", "")

DEFAULTS: dict[str, Any] = {
    "teacher": "pro",
    "dagger": True,
    "iterations": 8,          # data-collection / refit rounds sharing the step budget
    "beta_decay": 0.5,
    "epochs": 4,              # epochs over the aggregated data after each round (first round: 2x)
    "lr": 1e-3,
    "lr_final": 2e-4,         # learning rate of the last round (geometric decay)
    "batch_size": 2048,
    "weight_decay": 0.0,
    "max_samples": 1_500_000,  # aggregated dataset cap (random eviction beyond)
    "eval_every": 0,
}


def build_mlp(hidden: list[int], activation: str, out_dim: int, layernorm: bool = False,
              in_dim: int = 157) -> nn.Sequential:
    act = ACTIVATION_CLASSES[activation]
    mods: list[nn.Module] = []
    d = in_dim
    for h in hidden:
        mods.append(nn.Linear(d, h))
        if layernorm:
            mods.append(nn.LayerNorm(h))
        mods.append(act())
        d = h
    mods.append(nn.Linear(d, out_dim))
    return nn.Sequential(*mods)


class _Teachers:
    """One cached reference driver per arena, rebuilt when an arena's world changes."""

    def __init__(self, sim: LabSim, personality: str):
        self.sim = sim
        self.personality = personality
        self.drivers: list[CachedReferenceDriver | None] = []

    def act(self) -> np.ndarray:
        out = []
        if len(self.drivers) != len(self.sim.arenas):
            self.drivers = [None] * len(self.sim.arenas)
        for k, arena in enumerate(self.sim.arenas):
            d = self.drivers[k]
            if d is None or d.w is not arena.w:
                d = CachedReferenceDriver(arena.w, list(arena.learners), self.personality, laps=self.sim.cfg.laps)
                self.drivers[k] = d
            d.sync_with_world()
            out.append(d.act())
        return np.concatenate(out, axis=0)

    def reset_envs(self, envs: np.ndarray) -> None:
        for e in envs:
            a, row = self.sim.env_map[int(e)]
            if a < len(self.drivers) and self.drivers[a] is not None and self.drivers[a].w is self.sim.arenas[a].w:
                self.drivers[a].reset_rows([row])


def train_bc(cfg: dict[str, Any], regs: Regulations, init_brain: Brain | None, quota: QuotaCounter,
             progress: Callable[[dict], None] | None = None, should_stop: Callable[[], bool] | None = None,
             log_fn: Callable[[str], None] | None = None) -> tuple[Brain, dict[str, Any]]:
    hp = {**DEFAULTS, **cfg["hyperparams"]}
    arch = cfg["architecture"]
    teacher = str(hp["teacher"])
    if teacher not in PERSONALITIES:
        raise ValueError(f"professeur inconnu : {teacher!r} (attendu : {', '.join(PERSONALITIES)})")
    torch.manual_seed(int(cfg["seed"]))
    rng = np.random.default_rng(int(cfg["seed"]) + 17)
    out_dim = int(arch["outputs"])
    squash = arch["squash"]
    net = build_mlp(list(arch["hidden"]), arch["activation"], out_dim, bool(arch["layernorm"]))
    stats = ObsStats()
    if init_brain is not None:
        load_into_modules(init_brain, list(net))
        st = stats_from_brain(init_brain)
        if st is not None:
            stats.load(*st)
    budget = quota.remaining
    n_envs = max(1, min(int(cfg["envs"]), budget))
    sim = LabSim(settings_from_config(cfg, n=n_envs), regs, quota)
    teachers = _Teachers(sim, teacher)
    iters = max(1, int(hp["iterations"]))
    per_iter = max(n_envs, budget // iters)
    opt = torch.optim.Adam(net.parameters(), lr=float(hp["lr"]), weight_decay=float(hp["weight_decay"]))
    X = np.zeros((0, 157), dtype=np.float32)
    Y = np.zeros((0, out_dim), dtype=np.float32)
    curve: list[dict[str, Any]] = []
    t0 = time.time()
    obs = sim.reset()
    stop = False
    ep_returns: list[float] = []
    crashes = 0

    def learner_actions(o: np.ndarray) -> np.ndarray:
        with torch.no_grad():
            y = net(torch.as_tensor(stats.normalize(o))).numpy()
        return np.tanh(y) if squash == "tanh" else np.clip(y, -1.0, 1.0)

    for it in range(iters):
        if stop:
            break
        beta = 1.0 if (it == 0 or not hp["dagger"]) else float(hp["beta_decay"]) ** it
        if beta < 0.05:
            beta = 0.0
        target = min(per_iter if it < iters - 1 else quota.remaining, quota.remaining)
        steps = target // n_envs
        xs, ys = [], []
        for _ in range(steps):
            if should_stop is not None and should_stop():
                stop = True
                break
            label = teachers.act()[:, :out_dim].astype(np.float32)
            if beta >= 1.0:
                act = label.astype(np.float64)
            else:
                act = learner_actions(obs).astype(np.float64)
                use_t = rng.random(n_envs) < beta
                act[use_t] = label[use_t]
            xs.append(obs.copy())
            ys.append(label)
            try:
                obs, rew, term, trunc, infos = sim.step(act)
            except QuotaExceeded:
                stop = True
                break
            done = np.nonzero(term | trunc)[0]
            if len(done):
                teachers.reset_envs(done)
                for e in done:
                    ep = infos[int(e)].get("episode")
                    if ep:
                        ep_returns.append(ep["r"])
                        crashes += int(ep["reason"] not in NOT_CRASH)
        if not xs:
            break
        xb = np.concatenate(xs)
        yb = np.concatenate(ys)
        del xs, ys
        stats.update(xb)
        # aggregate; beyond the cap, evict random old samples
        room = int(hp["max_samples"]) - len(xb)
        if len(X) > room:
            keep = np.sort(rng.choice(len(X), max(room, 0), replace=False))
            X, Y = X[keep], Y[keep]
        X = np.concatenate([X, xb])
        Y = np.concatenate([Y, yb])
        del xb, yb
        n_data = len(X)
        lr = float(hp["lr"]) * (float(hp["lr_final"]) / float(hp["lr"])) ** (it / max(iters - 1, 1))
        for g in opt.param_groups:
            g["lr"] = lr
        epochs = int(hp["epochs"]) * (2 if it == 0 and init_brain is None else 1)
        loss = _fit(net, opt, X, Y, stats, epochs, int(hp["batch_size"]), squash)
        point = {"iteration": it, "beta": round(beta, 3), "steps": quota.used, "samples": n_data,
                 "loss": round(loss, 6), "mean_reward": round(float(np.mean(ep_returns[-200:])), 3) if ep_returns else None,
                 "crashes": crashes, "time": round(time.time() - t0, 1)}
        curve.append(point)
        crashes = 0
        if log_fn:
            log_fn(f"bc it {it} beta {beta:.3f} steps {quota.used} samples {n_data} loss {loss:.5f} t {time.time() - t0:.0f}s")
        if progress is not None:
            progress(dict(point))
    if not curve:
        # no data at all: still return the (possibly initial) network
        pass
    brain = export_sequential(list(net), squash, (stats.mean, stats.var, stats.count) if stats.count > 0 else None)
    log = {"curve": curve, "hyperparams": hp, "envs": n_envs, "duration": round(time.time() - t0, 1)}
    return brain, log


def _fit(net: nn.Module, opt: torch.optim.Optimizer, X: np.ndarray, Y: np.ndarray, stats: ObsStats,
         epochs: int, batch_size: int, squash: str) -> float:
    Xt = torch.from_numpy(np.ascontiguousarray(X, dtype=np.float32))
    Yt = torch.from_numpy(np.ascontiguousarray(Y, dtype=np.float32))
    mean = torch.as_tensor(stats.mean.astype(np.float32))
    inv = 1.0 / (torch.as_tensor(stats.std()) + 1e-8)
    n = len(Xt)
    last = 0.0
    net.train()
    for _ in range(max(1, epochs)):
        perm = torch.randperm(n)
        tot = 0.0
        for b in range(0, n, batch_size):
            idx = perm[b:b + batch_size]
            xb = torch.clamp((Xt[idx] - mean) * inv, -CLIP, CLIP)
            pred = net(xb)
            if squash == "tanh":
                pred = torch.tanh(pred)
            loss = ((pred - Yt[idx]) ** 2).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += float(loss.detach()) * len(idx)
        last = tot / n
    net.eval()
    return last
