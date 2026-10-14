"""OpenAI-style evolution strategies, with the whole population driving in one world.

Each generation samples ``population / 2`` Gaussian directions and evaluates the antithetic pairs
``theta ± sigma * eps`` as the cars of a single simulation (solo or race: one ghost world; traffic:
one small race per member). The two members of a pair start from exactly the same state (common
random numbers). Fitness is the episode return (same reward as every other method); fitnesses
are rank-shaped (centred ranks in [-0.5, 0.5]) and the gradient estimate feeds Adam, with L2
weight decay.

The population's networks are evaluated together: their parameters are stacked into
``(population, ...)`` arrays and every layer runs as one batched ``einsum`` (MLP, LayerNorm, GRU).
Observation statistics are updated from the cars' observations between generations (as in
Salimans et al. 2017) and exported in the brain's ``obs_norm``.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import numpy as np

from ...brains.spec import Brain, _act, mlp_brain
from ...config import Regulations
from ..envs import BrainResolver, LabSim, settings_from_config
from ..export import parse_mlp
from ..normalize import ObsStats, obs_norm_weights, stats_from_brain
from ..quota import QuotaCounter
from .bc import NOT_CRASH

DEFAULTS: dict[str, Any] = {
    "population": 64,          # even; antithetic pairs
    "sigma": 0.02,
    "lr": 0.01,
    "weight_decay": 0.005,
    "episode_decisions": None,  # default: curriculum.episode_decisions
    "obs_norm": "update",
    "obs_sample": 4,           # use every k-th decision's observations for the statistics
}


def param_keys(brain: Brain) -> list[str]:
    """Trainable weights of a brain (observation statistics excluded), in a fixed order."""
    norm = brain.spec.get("obs_norm") or {}
    skip = {norm.get(k) for k in ("mean", "std", "var")}
    return [k for k in sorted(brain.weights) if k not in skip]


class PopulationPolicy:
    """Evaluate ``P`` parameter vectors of the same brain architecture in one batched pass."""

    def __init__(self, template: Brain, keys: list[str]):
        self.spec = template.spec
        self.keys = keys
        self.shapes = [template.weights[k].shape for k in keys]
        sizes = [int(np.prod(s)) for s in self.shapes]
        self.offsets = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
        self.dim = int(self.offsets[-1])

    def unpack(self, thetas: np.ndarray) -> dict[str, np.ndarray]:
        P = thetas.shape[0]
        return {k: thetas[:, self.offsets[j]:self.offsets[j + 1]].reshape((P,) + tuple(self.shapes[j]))
                for j, k in enumerate(self.keys)}

    def init_state(self, P: int) -> list[np.ndarray]:
        states: list[np.ndarray] = []

        def walk(layers):
            for layer in layers:
                if layer["type"] == "gru":
                    states.append(np.zeros((P, int(layer["hidden"])), dtype=np.float32))
                elif layer["type"] == "residual":
                    walk(layer["layers"])
        walk(self.spec.get("layers", []))
        return states

    def forward(self, x: np.ndarray, W: dict[str, np.ndarray], state: list[np.ndarray]
                ) -> tuple[np.ndarray, list[np.ndarray]]:
        """``x``: (P, OBS_DIM) normalised observations, one row per member."""
        new_state: list[np.ndarray] = []
        counter = [0]
        y = self._run(self.spec.get("layers", []), x.astype(np.float32), W, state, new_state, counter)
        sq = self.spec.get("output", {}).get("squash", "tanh")
        if sq == "tanh":
            y = np.tanh(y)
        elif sq == "clip":
            y = np.clip(y, -1.0, 1.0)
        return y, new_state

    def _run(self, layers, x, W, state, new_state, counter):
        for layer in layers:
            t = layer["type"]
            if t == "linear":
                x = np.einsum("pi,pio->po", x, W[layer["w"]]) + W[layer["b"]]
            elif t == "layernorm":
                mu = x.mean(axis=-1, keepdims=True)
                var = x.var(axis=-1, keepdims=True)
                x = (x - mu) / np.sqrt(var + float(layer.get("eps", 1e-5)))
                x = x * W[layer["g"]] + W[layer["b"]]
            elif t == "gru":
                h = state[counter[0]]
                counter[0] += 1
                hid = int(layer["hidden"])
                gi = np.einsum("pi,pio->po", x, W[layer["wi"]]) + W[layer["bi"]]
                gh = np.einsum("pi,pio->po", h, W[layer["wh"]]) + W[layer["bh"]]
                r = 1.0 / (1.0 + np.exp(-(gi[:, :hid] + gh[:, :hid])))
                z = 1.0 / (1.0 + np.exp(-(gi[:, hid:2 * hid] + gh[:, hid:2 * hid])))
                nn_ = np.tanh(gi[:, 2 * hid:] + r * gh[:, 2 * hid:])
                h_new = ((1.0 - z) * nn_ + z * h).astype(np.float32)
                new_state.append(h_new)
                x = h_new
            elif t == "residual":
                x = x + self._run(layer["layers"], x, W, state, new_state, counter)
            else:
                x = _act(t, x)
        return x


def centered_ranks(x: np.ndarray) -> np.ndarray:
    ranks = np.empty(len(x), dtype=np.float64)
    ranks[np.argsort(x, kind="stable")] = np.arange(len(x))
    return ranks / max(len(x) - 1, 1) - 0.5


class Adam:
    def __init__(self, dim: int, lr: float, beta1: float = 0.9, beta2: float = 0.999, eps: float = 1e-8):
        self.lr, self.b1, self.b2, self.eps = lr, beta1, beta2, eps
        self.m = np.zeros(dim)
        self.v = np.zeros(dim)
        self.t = 0

    def step(self, grad: np.ndarray) -> np.ndarray:
        """Return the parameter increment for an *ascent* direction ``grad``."""
        self.t += 1
        self.m = self.b1 * self.m + (1 - self.b1) * grad
        self.v = self.b2 * self.v + (1 - self.b2) * grad * grad
        a = self.lr * np.sqrt(1 - self.b2 ** self.t) / (1 - self.b1 ** self.t)
        return a * self.m / (np.sqrt(self.v) + self.eps)


def _pair_starts(sim: LabSim, half: int) -> np.ndarray:
    """Copy the start state of car j into car j + half (solo/race ghost worlds)."""
    arena = sim.arenas[0]
    w = arena.w
    for j in range(half):
        w.S[j + half] = w.S[j]
        w.compound[j + half] = w.compound[j]
    w._refresh_compounds()
    arena.start_rows(np.arange(len(arena.learners)))
    return arena.observe()


def train_es(cfg: dict[str, Any], regs: Regulations, init_brain: Brain | None, quota: QuotaCounter,
             progress: Callable[[dict], None] | None = None, should_stop: Callable[[], bool] | None = None,
             brain_resolver: BrainResolver | None = None, log_fn: Callable[[str], None] | None = None
             ) -> tuple[Brain, dict[str, Any]]:
    hp = {**DEFAULTS, **cfg["hyperparams"]}
    arch = cfg["architecture"]
    seed = int(cfg["seed"])
    rng = np.random.default_rng(seed + 101)
    P = int(hp["population"])
    if P < 2 or P % 2:
        raise ValueError("es : la population doit être un nombre pair ≥ 2")
    half = P // 2
    if init_brain is not None:
        template = Brain(dict(init_brain.spec), dict(init_brain.weights))
    else:
        template = mlp_brain(list(arch["hidden"]), arch["activation"], int(arch["outputs"]), squash=arch["squash"],
                             rng=np.random.default_rng(seed), gru=int(arch["gru"]), layernorm=bool(arch["layernorm"]))
    keys = param_keys(template)
    pol = PopulationPolicy(template, keys)
    theta = np.concatenate([template.weights[k].ravel() for k in keys]).astype(np.float64)
    stats = ObsStats()
    if init_brain is not None:
        st = stats_from_brain(init_brain)
        if st is not None:
            stats.load(*st)
    update_norm = hp["obs_norm"] != "freeze" or stats.count == 0
    ep_len = int(hp["episode_decisions"] or cfg["curriculum"]["episode_decisions"])
    settings = settings_from_config(cfg, n=P)
    settings.episode_decisions = ep_len
    if settings.mode == "traffic":
        settings.learners_per_world = 1
    sim = LabSim(settings, regs, quota, brain_resolver, auto_reset=False)
    adam = Adam(len(theta), float(hp["lr"]))
    sigma = float(hp["sigma"])
    wd = float(hp["weight_decay"])
    rot_gens = max(1, int(settings.track_rotation) // max(ep_len, 1))
    curve: list[dict[str, Any]] = []
    t0 = time.time()
    gen = 0
    best_ret = -np.inf
    # A generation whose episodes are cut short by the quota compares members unfairly, so a new
    # generation starts only if a full one fits (the first one runs with whatever budget there is).
    while quota.can(P * ep_len) or (gen == 0 and quota.can(P)):
        if should_stop is not None and should_stop():
            break
        if gen > 0 and gen % rot_gens == 0:
            sim.track_k = sim._pick_track()
        obs = sim.reset()
        if settings.mode != "traffic":
            obs = _pair_starts(sim, half)
        eps = rng.standard_normal((half, len(theta)))
        thetas = np.concatenate([theta + sigma * eps, theta - sigma * eps]).astype(np.float32)
        W = pol.unpack(thetas)
        state = pol.init_state(P)
        active = np.ones(P, dtype=bool)
        fit = np.zeros(P)
        lengths = np.zeros(P, dtype=np.int64)
        crashed = np.zeros(P, dtype=bool)
        mean32 = stats.mean.astype(np.float32)
        std32 = stats.std() if stats.count > 0 else np.ones_like(mean32)
        obs_buf = []
        for t in range(ep_len):
            if not active.any() or not quota.can(int(active.sum())):
                break
            x = np.clip((obs - mean32) / (std32 + np.float32(1e-8)), -10.0, 10.0) if stats.count > 0 else obs
            a, state = pol.forward(x, W, state)
            obs, r, term, trunc, infos = sim.step(a, active=active)
            fit += np.where(active, r, 0.0)
            lengths += active
            done = (term | trunc) & active
            for i in np.nonzero(done)[0]:
                ep = infos[int(i)].get("episode") or {}
                crashed[i] = ep.get("reason") not in NOT_CRASH
            active &= ~(term | trunc)
            if update_norm and t % int(hp["obs_sample"]) == 0 and active.any():
                obs_buf.append(obs[active])
        if update_norm and obs_buf:
            stats.update(np.concatenate(obs_buf))
        u = centered_ranks(fit)
        grad = (u[:half] - u[half:]) @ eps / (P * sigma)
        theta += adam.step(grad - wd * theta)
        gen += 1
        best_ret = max(best_ret, float(fit.max()))
        point = {"generation": gen, "steps": quota.used, "mean_reward": round(float(fit.mean()), 3),
                 "max_reward": round(float(fit.max()), 3), "crash_rate": round(float(crashed.mean()), 3),
                 "mean_length": round(float(lengths.mean()), 1), "time": round(time.time() - t0, 1)}
        curve.append(point)
        if log_fn and (gen % 5 == 0 or gen == 1):
            log_fn(" ".join(f"{k} {v}" for k, v in point.items()))
        if progress is not None:
            progress(dict(point))
    # final brain: the distribution mean
    weights = {k: v[0].copy() for k, v in pol.unpack(theta[None, :].astype(np.float32)).items()}
    spec = {k: v for k, v in template.spec.items() if k != "obs_norm"}
    spec["meta"] = {}
    if stats.count > 0:
        norm, nw = obs_norm_weights(stats.mean, stats.var, stats.count)
        spec["obs_norm"] = norm
        weights.update(nw)
    brain = Brain(spec, weights).validate()
    parse_mlp(brain)  # sanity: still an MLP/GRU brain the lab understands
    log = {"curve": curve, "hyperparams": hp, "population": P, "generations": gen,
           "duration": round(time.time() - t0, 1)}
    return brain, log
