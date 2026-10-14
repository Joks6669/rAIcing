"""PPO / SAC / TD3 (stable-baselines3) on the lab's vectorised environment.

The environment is a ``LabVecEnv`` (one env per learner car, all cars of a solo world stepping
together) wrapped in ``LabVecNormalize`` (observation statistics exported into the brain, optional
reward scaling). A callback stops training cooperatively at the step budget, on quota exhaustion
or on ``should_stop()``, and reports progress.

Continuing from a brain (``init_from``) loads its weights into the policy (PPO: policy network and
action head; SAC: ``latent_pi`` + ``mu``; TD3: ``actor.mu`` and its target), restores its observation
statistics (frozen by default) and starts with a small exploration noise. For PPO the critic is
first trained alone for ``critic_warmup`` rollouts so that a fresh value function does not wreck a
competent policy.
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable

import numpy as np
import torch
from stable_baselines3 import PPO, SAC, TD3
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.noise import NormalActionNoise

from ...brains.spec import Brain
from ...config import Regulations
from ..envs import BrainResolver, LabSim, LabVecEnv, settings_from_config
from ..export import ACTIVATION_CLASSES, export_ppo, export_sac, export_td3, load_into_modules
from ..normalize import LabVecNormalize, stats_from_brain
from ..quota import QuotaCounter, QuotaExceeded
from .bc import NOT_CRASH

PPO_DEFAULTS: dict[str, Any] = {
    "lr": 3e-4, "lr_final": None, "n_steps": 256, "batch_size": 4096, "n_epochs": 5, "gamma": 0.99,
    "gae_lambda": 0.95, "clip_range": 0.2, "ent_coef": 0.0, "vf_coef": 0.5, "max_grad_norm": 0.5,
    "target_kl": None, "log_std_init": -0.5, "norm_reward": True, "obs_norm": "update",
    "critic_warmup": 0,
}
PPO_FINETUNE: dict[str, Any] = {"lr": 5e-5, "log_std_init": -2.0, "obs_norm": "freeze", "critic_warmup": 4,
                                "target_kl": 0.02}
SAC_DEFAULTS: dict[str, Any] = {
    "lr": 3e-4, "buffer_size": 300_000, "batch_size": 512, "gamma": 0.99, "tau": 0.005,
    "learning_starts": 10_000, "train_freq": 1, "gradient_steps": 4, "ent_coef": "auto",
    "log_std_init": None, "norm_reward": False, "obs_norm": "update", "actor_warmup": 0,
}
SAC_FINETUNE: dict[str, Any] = {"lr": 1e-4, "learning_starts": 0, "log_std_init": -2.5, "obs_norm": "freeze",
                                "ent_coef": 0.002, "actor_warmup": 20_000}
TD3_DEFAULTS: dict[str, Any] = {
    "lr": 3e-4, "buffer_size": 300_000, "batch_size": 512, "gamma": 0.99, "tau": 0.005,
    "learning_starts": 10_000, "train_freq": 1, "gradient_steps": 4, "action_noise": 0.1,
    "policy_delay": 2, "target_policy_noise": 0.2, "target_noise_clip": 0.5, "norm_reward": False,
    "obs_norm": "update", "actor_warmup": 0,
}
TD3_FINETUNE: dict[str, Any] = {"lr": 1e-4, "learning_starts": 0, "action_noise": 0.05, "obs_norm": "freeze",
                                "actor_warmup": 20_000}


def method_defaults(method: str, finetune: bool) -> dict[str, Any]:
    base = {"ppo": PPO_DEFAULTS, "sac": SAC_DEFAULTS, "td3": TD3_DEFAULTS}[method]
    tune = {"ppo": PPO_FINETUNE, "sac": SAC_FINETUNE, "td3": TD3_FINETUNE}[method]
    return {**base, **(tune if finetune else {})}


class LabCallback(BaseCallback):
    """Budget / quota / cancellation guard, progress reports and the training curve."""

    def __init__(self, quota: QuotaCounter, n_envs: int, should_stop: Callable[[], bool] | None,
                 progress: Callable[[dict], None] | None, log_fn: Callable[[str], None] | None,
                 report_every: int = 50_000, freeze_actor: list[torch.nn.Parameter] | None = None,
                 unfreeze_after: int = 0, unfreeze_on_rollouts: bool = True):
        super().__init__()
        self.quota = quota
        self.n_envs = n_envs
        self.should_stop = should_stop
        self.progress_cb = progress
        self.log_fn = log_fn
        self.report_every = report_every
        self.next_report = report_every
        self.curve: list[dict[str, Any]] = []
        self.ep_returns: list[float] = []
        self.ep_lengths: list[int] = []
        self.ep_laps: list[int] = []
        self.ep_crash: list[bool] = []
        self.t0 = time.time()
        self.frozen = list(freeze_actor or [])
        self.unfreeze_after = unfreeze_after
        self.unfreeze_on_rollouts = unfreeze_on_rollouts
        self.rollouts = 0
        self.stopped_reason = ""
        if self.frozen and unfreeze_after > 0:
            for p in self.frozen:
                p.requires_grad_(False)
        else:
            self.frozen = []

    def _unfreeze(self) -> None:
        for p in self.frozen:
            p.requires_grad_(True)
        self.frozen = []
        if self.log_fn:
            self.log_fn("actor unfrozen")

    def _on_rollout_end(self) -> None:
        self.rollouts += 1
        if self.frozen and self.unfreeze_on_rollouts and self.rollouts > self.unfreeze_after:
            self._unfreeze()

    def _on_step(self) -> bool:
        for info in self.locals.get("infos", []):
            ep = info.get("episode")
            if ep:
                self.ep_returns.append(float(ep["r"]))
                self.ep_lengths.append(int(ep["l"]))
                self.ep_laps.append(int(ep.get("laps", 0)))
                self.ep_crash.append(ep.get("reason") not in NOT_CRASH)
        if self.frozen and not self.unfreeze_on_rollouts and self.quota.used >= self.unfreeze_after:
            self._unfreeze()
        if self.quota.used >= self.next_report:
            self.next_report += self.report_every
            self._report()
        if self.should_stop is not None and self.should_stop():
            self.stopped_reason = "cancelled"
            return False
        if not self.quota.can(self.n_envs):
            self.stopped_reason = "budget"
            return False
        return True

    def _report(self) -> None:
        r = self.ep_returns[-200:]
        point = {"steps": self.quota.used, "time": round(time.time() - self.t0, 1),
                 "mean_reward": round(float(np.mean(r)), 3) if r else None,
                 "mean_length": round(float(np.mean(self.ep_lengths[-200:])), 1) if r else None,
                 "crash_rate": round(float(np.mean(self.ep_crash[-200:])), 3) if r else None,
                 "episodes": len(self.ep_returns)}
        self.curve.append(point)
        if self.log_fn:
            self.log_fn(" ".join(f"{k} {v}" for k, v in point.items()))
        if self.progress_cb is not None:
            self.progress_cb(dict(point))


def _net_arch(arch: dict[str, Any], method: str) -> dict[str, Any]:
    if arch["gru"]:
        raise ValueError(f"la méthode {method} ne prend pas en charge les couches GRU (utilisez es)")
    if arch["layernorm"]:
        raise ValueError(f"la méthode {method} ne prend pas en charge layernorm (utilisez es ou bc)")
    hidden = list(arch["hidden"])
    if method == "ppo":
        return {"net_arch": dict(pi=hidden, vf=hidden), "activation_fn": ACTIVATION_CLASSES[arch["activation"]]}
    return {"net_arch": dict(pi=hidden, qf=hidden), "activation_fn": ACTIVATION_CLASSES[arch["activation"]]}


def train_sb3(method: str, cfg: dict[str, Any], regs: Regulations, init_brain: Brain | None, quota: QuotaCounter,
              progress: Callable[[dict], None] | None = None, should_stop: Callable[[], bool] | None = None,
              brain_resolver: BrainResolver | None = None, log_fn: Callable[[str], None] | None = None,
              report_every: int = 50_000) -> tuple[Brain, dict[str, Any]]:
    hp = {**method_defaults(method, init_brain is not None), **cfg["hyperparams"]}
    arch = cfg["architecture"]
    seed = int(cfg["seed"])
    torch.manual_seed(seed)
    budget = quota.remaining
    n_envs = max(1, min(int(cfg["envs"]), budget))
    sim = LabSim(settings_from_config(cfg, n=n_envs), regs, quota, brain_resolver)
    st = stats_from_brain(init_brain) if init_brain is not None else None
    venv = LabVecNormalize(LabVecEnv(sim), freeze_obs=hp["obs_norm"] == "freeze" and st is not None,
                           norm_reward=bool(hp["norm_reward"]), gamma=float(hp["gamma"]))
    if st is not None:
        venv.load_stats(*st)
    pk = _net_arch(arch, method)
    lr = float(hp["lr"])
    lr_sched: Any = lr
    if hp.get("lr_final") is not None:
        lr_final = float(hp["lr_final"])
        lr_sched = lambda frac: lr_final + (lr - lr_final) * frac  # noqa: E731  (frac: 1 -> 0)
    common = dict(env=venv, gamma=float(hp["gamma"]), seed=seed, device="cpu", verbose=0)
    freeze: list[torch.nn.Parameter] = []
    unfreeze_after = 0
    by_rollouts = True
    if method == "ppo":
        n_steps = int(hp["n_steps"])
        batch = min(int(hp["batch_size"]), n_steps * n_envs)
        pk["log_std_init"] = float(hp["log_std_init"])
        model = PPO("MlpPolicy", learning_rate=lr_sched, n_steps=n_steps, batch_size=batch,
                    n_epochs=int(hp["n_epochs"]), gae_lambda=float(hp["gae_lambda"]), clip_range=float(hp["clip_range"]),
                    ent_coef=float(hp["ent_coef"]), vf_coef=float(hp["vf_coef"]), max_grad_norm=float(hp["max_grad_norm"]),
                    target_kl=hp["target_kl"], policy_kwargs=pk, **common)
        pol = model.policy
        actor_mods = list(pol.mlp_extractor.policy_net) + [pol.action_net]
        if init_brain is not None:
            load_into_modules(init_brain, actor_mods)
        freeze = [p for m in actor_mods for p in m.parameters()] + [pol.log_std]
        unfreeze_after = int(hp["critic_warmup"])
    elif method == "sac":
        model = SAC("MlpPolicy", learning_rate=lr_sched, buffer_size=int(hp["buffer_size"]),
                    batch_size=int(hp["batch_size"]), tau=float(hp["tau"]), learning_starts=int(hp["learning_starts"]),
                    train_freq=int(hp["train_freq"]), gradient_steps=int(hp["gradient_steps"]),
                    ent_coef=hp["ent_coef"], policy_kwargs=pk, **common)
        actor = model.actor
        if init_brain is not None:
            load_into_modules(init_brain, list(actor.latent_pi) + [actor.mu])
        if hp.get("log_std_init") is not None:
            with torch.no_grad():
                actor.log_std.weight.mul_(0.01)
                actor.log_std.bias.fill_(float(hp["log_std_init"]))
        freeze = [p for m in list(actor.latent_pi) + [actor.mu, actor.log_std] for p in m.parameters()]
        unfreeze_after = int(hp["actor_warmup"])
        by_rollouts = False
    elif method == "td3":
        out_dim = int(arch["outputs"])
        noise = NormalActionNoise(np.zeros(out_dim), float(hp["action_noise"]) * np.ones(out_dim))
        model = TD3("MlpPolicy", learning_rate=lr_sched, buffer_size=int(hp["buffer_size"]),
                    batch_size=int(hp["batch_size"]), tau=float(hp["tau"]), learning_starts=int(hp["learning_starts"]),
                    train_freq=int(hp["train_freq"]), gradient_steps=int(hp["gradient_steps"]), action_noise=noise,
                    policy_delay=int(hp["policy_delay"]), target_policy_noise=float(hp["target_policy_noise"]),
                    target_noise_clip=float(hp["target_noise_clip"]), policy_kwargs=pk, **common)
        if init_brain is not None:
            mods = [m for m in model.actor.mu if not isinstance(m, torch.nn.Tanh)]
            load_into_modules(init_brain, mods)
            model.actor_target.load_state_dict(model.actor.state_dict())
        freeze = list(model.actor.parameters())
        unfreeze_after = int(hp["actor_warmup"])
        by_rollouts = False
    else:  # pragma: no cover - guarded by validate_config
        raise ValueError(f"méthode inconnue {method!r}")
    cb = LabCallback(quota, n_envs, should_stop, progress, log_fn, report_every=report_every,
                     freeze_actor=freeze if init_brain is not None else None, unfreeze_after=unfreeze_after,
                     unfreeze_on_rollouts=by_rollouts)
    t0 = time.time()
    stopped = ""
    try:
        model.learn(total_timesteps=max(budget, 1), callback=cb, progress_bar=False)
    except QuotaExceeded:
        stopped = "quota"
    if cb.frozen:
        cb._unfreeze()
    cb._report()
    stats = venv.stats()
    exporter = {"ppo": export_ppo, "sac": export_sac, "td3": export_td3}[method]
    brain = exporter(model, stats)
    log = {"curve": cb.curve, "hyperparams": {k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v))
                                              for k, v in hp.items()},
           "envs": n_envs, "duration": round(time.time() - t0, 1), "stopped": stopped or cb.stopped_reason or "done",
           "episodes": len(cb.ep_returns)}
    if not math.isfinite(sum(p.sum().item() for p in model.policy.parameters())):
        raise RuntimeError("l'entraînement a divergé (poids non finis)")
    return brain, log
