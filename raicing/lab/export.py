"""Torch policies -> declarative brains (and back, to continue training).

Every exporter produces a validated ``Brain`` whose deterministic action equals the trained
policy's deterministic action (``tests/test_lab.py`` checks it numerically):

* PPO: ``policy.mlp_extractor.policy_net`` + ``policy.action_net``; SB3 clips the Gaussian mean to the
  action box, hence squash ``clip``.
* SAC: ``actor.latent_pi`` + ``actor.mu``; the squashed Gaussian mean is ``tanh(mu)``, squash ``tanh``.
* TD3: ``actor.mu`` is ``Linear/act/.../Linear/Tanh``; the final ``Tanh`` becomes squash ``tanh``.
* Plain torch ``nn.Sequential`` MLPs (behaviour cloning), with optional ``LayerNorm``.

Observation statistics (``normalize.obs_norm_weights``) are embedded as ``obs_norm``.
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import torch
import torch.nn as nn

from ..brains.spec import FORMAT, Brain, BrainError
from ..sensors import OBS_DIM, OBS_VERSION
from .normalize import obs_norm_weights

# torch module class -> brain activation layer
_TORCH_ACT: dict[type, str] = {nn.Tanh: "tanh", nn.ReLU: "relu", nn.ELU: "elu", nn.SiLU: "silu",
                               nn.Sigmoid: "sigmoid", nn.GELU: "gelu"}


class GeluTanh(nn.GELU):
    """GELU with the tanh approximation, which is what the brain runtime computes."""

    def __init__(self) -> None:
        super().__init__(approximate="tanh")


ACTIVATION_CLASSES: dict[str, type[nn.Module]] = {"tanh": nn.Tanh, "relu": nn.ReLU, "elu": nn.ELU,
                                                  "silu": nn.SiLU, "sigmoid": nn.Sigmoid, "gelu": GeluTanh}


def _act_name(m: nn.Module) -> str | None:
    if isinstance(m, nn.GELU):
        if m.approximate != "tanh":
            raise BrainError("GELU must use approximate='tanh' to be exported exactly")
        return "gelu"
    for cls, name in _TORCH_ACT.items():
        if isinstance(m, cls):
            return name
    return None


def flatten_modules(modules: Sequence[nn.Module]) -> list[nn.Module]:
    """Flatten nested ``nn.Sequential`` containers, dropping identities and flattens."""
    out: list[nn.Module] = []
    for m in modules:
        if isinstance(m, nn.Sequential):
            out.extend(flatten_modules(list(m)))
        elif isinstance(m, (nn.Identity, nn.Flatten, nn.Dropout)):
            continue
        else:
            out.append(m)
    return out


def layers_from_modules(modules: Sequence[nn.Module], prefix: str = "") -> tuple[list[dict], dict[str, np.ndarray]]:
    """Translate Linear / LayerNorm / activation modules into brain layers + weights."""
    layers: list[dict] = []
    weights: dict[str, np.ndarray] = {}
    li = 0
    for m in flatten_modules(modules):
        if isinstance(m, nn.Linear):
            wn, bn = f"{prefix}l{li}.w", f"{prefix}l{li}.b"
            weights[wn] = m.weight.detach().cpu().numpy().T.astype(np.float32).copy()
            b = m.bias.detach().cpu().numpy() if m.bias is not None else np.zeros(m.out_features)
            weights[bn] = b.astype(np.float32).copy()
            layers.append({"type": "linear", "w": wn, "b": bn})
            li += 1
        elif isinstance(m, nn.LayerNorm):
            if not m.elementwise_affine:
                raise BrainError("LayerNorm must be elementwise_affine to be exported")
            gn, bn = f"{prefix}ln{li}.g", f"{prefix}ln{li}.b"
            weights[gn] = m.weight.detach().cpu().numpy().astype(np.float32).copy()
            weights[bn] = m.bias.detach().cpu().numpy().astype(np.float32).copy()
            layers.append({"type": "layernorm", "g": gn, "b": bn, "eps": float(m.eps)})
        else:
            name = _act_name(m)
            if name is None:
                raise BrainError(f"module {type(m).__name__} cannot be exported to a brain")
            layers.append({"type": name})
    return layers, weights


def make_brain(layers: list[dict], weights: dict[str, np.ndarray], out_dim: int, squash: str,
               obs_stats: tuple[np.ndarray, np.ndarray, float] | None, meta: dict[str, Any] | None = None) -> Brain:
    spec: dict[str, Any] = {"format": FORMAT, "obs_version": OBS_VERSION, "input_dim": OBS_DIM, "layers": layers,
                            "output": {"dim": int(out_dim), "squash": squash}, "meta": dict(meta or {})}
    weights = dict(weights)
    if obs_stats is not None:
        norm, nw = obs_norm_weights(*obs_stats)
        spec["obs_norm"] = norm
        weights.update(nw)
    return Brain(spec, weights).validate()


def export_sequential(modules: Sequence[nn.Module], squash: str, obs_stats=None, meta=None) -> Brain:
    """Export a plain torch MLP (``nn.Sequential`` or list of modules) as a brain."""
    layers, weights = layers_from_modules(modules)
    last = [m for m in flatten_modules(modules) if isinstance(m, nn.Linear)][-1]
    return make_brain(layers, weights, last.out_features, squash, obs_stats, meta)


def export_ppo(model, obs_stats=None, meta=None) -> Brain:
    """SB3 PPO (MlpPolicy, Box actions in [-1, 1]) -> brain with squash ``clip``."""
    pol = model.policy
    if getattr(pol, "use_sde", False):
        raise BrainError("PPO with gSDE: the exported mean action is still clip(action_net(latent))")
    mods = list(pol.mlp_extractor.policy_net) + [pol.action_net]
    return export_sequential(mods, "clip", obs_stats, meta)


def export_sac(model, obs_stats=None, meta=None) -> Brain:
    """SB3 SAC -> brain with squash ``tanh`` (deterministic action = tanh(mu(latent_pi(obs))))."""
    actor = model.actor
    mods = list(actor.latent_pi) + [actor.mu]
    return export_sequential(mods, "tanh", obs_stats, meta)


def export_td3(model, obs_stats=None, meta=None) -> Brain:
    """SB3 TD3 -> brain. ``actor.mu`` ends with ``nn.Tanh``, exported as squash ``tanh``."""
    mods = flatten_modules(list(model.actor.mu))
    if not isinstance(mods[-1], nn.Tanh):
        raise BrainError("TD3 actor is expected to end with Tanh")
    return export_sequential(mods[:-1], "tanh", obs_stats, meta)


# ---------------------------------------------------------------------- brain -> torch
def parse_mlp(brain: Brain) -> dict[str, Any]:
    """Describe a brain as an MLP architecture, or raise ``BrainError`` if it is not one.

    Returns ``{"hidden": [...], "activation": str, "layernorm": bool, "gru": int, "outputs": int,
    "squash": str}`` (``gru`` > 0 when the brain has a GRU layer right before the output layer)."""
    layers = brain.spec.get("layers", [])
    hidden: list[int] = []
    acts: set[str] = set()
    layernorm = False
    gru = 0
    linears = []
    for k, layer in enumerate(layers):
        t = layer["type"]
        if t == "linear":
            linears.append(layer)
            hidden.append(int(brain.weights[layer["w"]].shape[1]))
        elif t == "layernorm":
            layernorm = True
        elif t == "gru":
            if gru:
                raise BrainError("only one GRU layer is supported")
            gru = int(layer["hidden"])
        elif t == "residual":
            raise BrainError("residual blocks are not supported by the lab trainers")
        else:
            acts.add(t)
    if not linears:
        raise BrainError("brain has no linear layer")
    if len(acts) > 1:
        raise BrainError("mixed activations are not supported by the lab trainers")
    outputs = hidden.pop()
    return {"hidden": hidden, "activation": acts.pop() if acts else "tanh", "layernorm": layernorm, "gru": gru,
            "outputs": outputs, "squash": brain.spec.get("output", {}).get("squash", "tanh")}


def load_into_modules(brain: Brain, modules: Sequence[nn.Module]) -> None:
    """Copy a brain's Linear / LayerNorm weights into matching torch modules (same order and shapes)."""
    mods = [m for m in flatten_modules(modules) if isinstance(m, (nn.Linear, nn.LayerNorm))]
    blayers = [lay for lay in brain.spec.get("layers", []) if lay["type"] in ("linear", "layernorm")]
    if len(mods) != len(blayers):
        raise BrainError(f"architecture mismatch: {len(blayers)} parametrised layers in the brain, {len(mods)} in the model")
    with torch.no_grad():
        for m, lay in zip(mods, blayers):
            if isinstance(m, nn.Linear):
                if lay["type"] != "linear":
                    raise BrainError("architecture mismatch (linear vs layernorm)")
                W = torch.as_tensor(brain.weights[lay["w"]].T.copy())
                if tuple(W.shape) != tuple(m.weight.shape):
                    raise BrainError(f"shape mismatch {tuple(W.shape)} vs {tuple(m.weight.shape)}")
                m.weight.copy_(W)
                m.bias.copy_(torch.as_tensor(brain.weights[lay["b"]]))
            else:
                if lay["type"] != "layernorm":
                    raise BrainError("architecture mismatch (layernorm vs linear)")
                m.weight.copy_(torch.as_tensor(brain.weights[lay["g"]]))
                m.bias.copy_(torch.as_tensor(brain.weights[lay["b"]]))
