"""Declarative neural "brains": architecture JSON + weights, executed in numpy.

A brain package is a directory (or a dict in memory) containing:

* ``brain.json`` — ``{"format": "raicing-brain/1", "obs_version": 1, "input_dim": ..., "layers": [...],
  "output": {"dim": 2..11, "squash": "tanh" | "clip" | "none"}, "obs_norm": {...}, "meta": {...}}``
* ``weights.npz`` — named float32 arrays referenced by the layers.

Supported layers: ``linear``, ``relu``, ``tanh``, ``gelu``, ``sigmoid``, ``elu``, ``silu``,
``layernorm``, ``gru`` (recurrent memory) and ``residual`` (a block of layers added to its input).
No arbitrary code is ever executed, so brains are safe to run on the league servers.
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..sensors import OBS_DIM, OBS_VERSION
from ..world import ACT_DIM

FORMAT = "raicing-brain/1"
ACTIVATIONS = {"relu", "tanh", "gelu", "sigmoid", "elu", "silu"}


class BrainError(ValueError):
    pass


def _act(name: str, x: np.ndarray) -> np.ndarray:
    if name == "relu":
        return np.maximum(x, 0.0)
    if name == "tanh":
        return np.tanh(x)
    if name == "sigmoid":
        return 1.0 / (1.0 + np.exp(-x))
    if name == "elu":
        return np.where(x > 0, x, np.expm1(np.minimum(x, 0.0)))
    if name == "silu":
        return x / (1.0 + np.exp(-x))
    if name == "gelu":
        return 0.5 * x * (1.0 + np.tanh(0.7978845608 * (x + 0.044715 * x ** 3)))
    raise BrainError(f"unknown activation {name}")


@dataclass
class Brain:
    spec: dict[str, Any]
    weights: dict[str, np.ndarray] = field(default_factory=dict)

    # ------------------------------------------------------------------ validation
    def validate(self, max_params: int = 2_000_000, max_layers: int = 48) -> "Brain":
        sp = self.spec
        if sp.get("format") != FORMAT:
            raise BrainError(f"format must be {FORMAT}")
        if int(sp.get("obs_version", -1)) != OBS_VERSION:
            raise BrainError(f"obs_version must be {OBS_VERSION}")
        in_dim = int(sp.get("input_dim", OBS_DIM))
        if in_dim != OBS_DIM:
            raise BrainError(f"input_dim must be {OBS_DIM}")
        out = sp.get("output", {})
        out_dim = int(out.get("dim", 2))
        if not 2 <= out_dim <= ACT_DIM:
            raise BrainError(f"output.dim must be between 2 and {ACT_DIM}")
        if out.get("squash", "tanh") not in ("tanh", "clip", "none"):
            raise BrainError("output.squash must be tanh, clip or none")
        for k, v in list(self.weights.items()):
            arr = np.asarray(v, dtype=np.float32)
            if not np.all(np.isfinite(arr)):
                raise BrainError(f"weight {k} contains NaN/inf")
            self.weights[k] = arr
        count = [0]
        dim = self._check_layers(sp.get("layers", []), in_dim, count)
        if dim != out_dim:
            raise BrainError(f"network outputs {dim} values but output.dim is {out_dim}")
        if count[0] > max_layers:
            raise BrainError(f"too many layers ({count[0]} > {max_layers})")
        n_params = self.param_count()
        if n_params > max_params:
            raise BrainError(f"too many parameters ({n_params} > {max_params})")
        norm = sp.get("obs_norm")
        if norm:
            for key in ("mean", "std"):
                if self._w(norm[key]).shape != (in_dim,):
                    raise BrainError(f"obs_norm.{key} must have shape ({in_dim},)")
        return self

    def _w(self, name: str) -> np.ndarray:
        if name not in self.weights:
            raise BrainError(f"missing weight '{name}'")
        return self.weights[name]

    def _check_layers(self, layers: list, dim: int, count: list[int]) -> int:
        for layer in layers:
            count[0] += 1
            t = layer.get("type")
            if t == "linear":
                W, b = self._w(layer["w"]), self._w(layer["b"])
                if W.shape != (dim, W.shape[1]) or b.shape != (W.shape[1],):
                    raise BrainError(f"linear layer shapes {W.shape}/{b.shape} incompatible with input {dim}")
                dim = W.shape[1]
            elif t in ACTIVATIONS:
                pass
            elif t == "layernorm":
                if self._w(layer["g"]).shape != (dim,) or self._w(layer["b"]).shape != (dim,):
                    raise BrainError("layernorm shapes")
            elif t == "gru":
                h = int(layer["hidden"])
                if self._w(layer["wi"]).shape != (dim, 3 * h) or self._w(layer["wh"]).shape != (h, 3 * h):
                    raise BrainError("gru weight shapes")
                if self._w(layer["bi"]).shape != (3 * h,) or self._w(layer["bh"]).shape != (3 * h,):
                    raise BrainError("gru bias shapes")
                dim = h
            elif t == "residual":
                inner = self._check_layers(layer.get("layers", []), dim, count)
                if inner != dim:
                    raise BrainError("residual block must preserve the dimension")
            else:
                raise BrainError(f"unknown layer type {t!r}")
        return dim

    def param_count(self) -> int:
        return int(sum(np.asarray(v).size for v in self.weights.values()))

    @property
    def output_dim(self) -> int:
        return int(self.spec.get("output", {}).get("dim", 2))

    @property
    def meta(self) -> dict[str, Any]:
        return self.spec.setdefault("meta", {})

    # ------------------------------------------------------------------ inference
    def init_state(self, n: int) -> list[np.ndarray]:
        states: list[np.ndarray] = []

        def walk(layers):
            for layer in layers:
                if layer.get("type") == "gru":
                    states.append(np.zeros((n, int(layer["hidden"])), dtype=np.float32))
                elif layer.get("type") == "residual":
                    walk(layer.get("layers", []))
        walk(self.spec.get("layers", []))
        return states

    def forward(self, obs: np.ndarray, state: list[np.ndarray] | None = None) -> tuple[np.ndarray, list[np.ndarray]]:
        x = np.asarray(obs, dtype=np.float32)
        if x.ndim == 1:
            x = x[None, :]
        norm = self.spec.get("obs_norm")
        if norm:
            clip = float(norm.get("clip", 10.0))
            x = np.clip((x - self.weights[norm["mean"]]) / (self.weights[norm["std"]] + 1e-8), -clip, clip)
        state = list(state) if state is not None else self.init_state(len(x))
        new_state: list[np.ndarray] = []
        counter = [0]
        x = self._run(self.spec.get("layers", []), x, state, new_state, counter)
        sq = self.spec.get("output", {}).get("squash", "tanh")
        if sq == "tanh":
            x = np.tanh(x)
        elif sq == "clip":
            x = np.clip(x, -1.0, 1.0)
        return x, new_state

    def _run(self, layers, x, state, new_state, counter):
        for layer in layers:
            t = layer["type"]
            if t == "linear":
                x = x @ self.weights[layer["w"]] + self.weights[layer["b"]]
            elif t in ACTIVATIONS:
                x = _act(t, x)
            elif t == "layernorm":
                mu = x.mean(axis=-1, keepdims=True)
                var = x.var(axis=-1, keepdims=True)
                x = (x - mu) / np.sqrt(var + float(layer.get("eps", 1e-5)))
                x = x * self.weights[layer["g"]] + self.weights[layer["b"]]
            elif t == "gru":
                h = state[counter[0]]
                counter[0] += 1
                hid = int(layer["hidden"])
                gi = x @ self.weights[layer["wi"]] + self.weights[layer["bi"]]
                gh = h @ self.weights[layer["wh"]] + self.weights[layer["bh"]]
                r = 1.0 / (1.0 + np.exp(-(gi[:, :hid] + gh[:, :hid])))
                z = 1.0 / (1.0 + np.exp(-(gi[:, hid:2 * hid] + gh[:, hid:2 * hid])))
                nn_ = np.tanh(gi[:, 2 * hid:] + r * gh[:, 2 * hid:])
                h_new = ((1.0 - z) * nn_ + z * h).astype(np.float32)
                new_state.append(h_new)
                x = h_new
            elif t == "residual":
                x = x + self._run(layer["layers"], x, state, new_state, counter)
        return x

    def act(self, obs: np.ndarray, state: list[np.ndarray] | None = None) -> tuple[np.ndarray, list[np.ndarray]]:
        """Full ACT_DIM action matrix (missing outputs are zero; pit request defaults to 'no')."""
        y, st = self.forward(obs, state)
        out = np.zeros((len(y), ACT_DIM), dtype=np.float32)
        out[:, : y.shape[1]] = y
        if y.shape[1] <= 2:
            out[:, 2] = -1.0
        return out, st

    # ------------------------------------------------------------------ persistence
    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        (p / "brain.json").write_text(json.dumps(self.spec, indent=1, ensure_ascii=False))
        np.savez_compressed(p / "weights.npz", **self.weights)
        return p

    @classmethod
    def load(cls, path: str | Path) -> "Brain":
        p = Path(path)
        spec = json.loads((p / "brain.json").read_text())
        with np.load(p / "weights.npz") as z:
            weights = {k: z[k].astype(np.float32) for k in z.files}
        return cls(spec, weights)

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("brain.json", json.dumps(self.spec, ensure_ascii=False))
            wbuf = io.BytesIO()
            np.savez(wbuf, **self.weights)
            zf.writestr("weights.npz", wbuf.getvalue())
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> "Brain":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            spec = json.loads(zf.read("brain.json"))
            with np.load(io.BytesIO(zf.read("weights.npz"))) as z:
                weights = {k: z[k].astype(np.float32) for k in z.files}
        return cls(spec, weights)


# ---------------------------------------------------------------------- builders
def mlp_brain(hidden: list[int], activation: str = "tanh", out_dim: int = 2, squash: str = "tanh",
              rng: np.random.Generator | None = None, gru: int = 0, layernorm: bool = False,
              meta: dict | None = None) -> Brain:
    """Randomly initialised MLP (optionally with a GRU memory layer) — used by ES and as a template."""
    rng = rng or np.random.default_rng(0)
    layers: list[dict] = []
    weights: dict[str, np.ndarray] = {}
    dim = OBS_DIM
    for li, h in enumerate(hidden):
        W = rng.standard_normal((dim, h)).astype(np.float32) * np.float32(np.sqrt(1.0 / dim))
        weights[f"l{li}.w"] = W
        weights[f"l{li}.b"] = np.zeros(h, dtype=np.float32)
        layers.append({"type": "linear", "w": f"l{li}.w", "b": f"l{li}.b"})
        if layernorm:
            weights[f"ln{li}.g"] = np.ones(h, dtype=np.float32)
            weights[f"ln{li}.b"] = np.zeros(h, dtype=np.float32)
            layers.append({"type": "layernorm", "g": f"ln{li}.g", "b": f"ln{li}.b"})
        layers.append({"type": activation})
        dim = h
    if gru:
        s = np.float32(np.sqrt(1.0 / gru))
        weights["gru.wi"] = rng.uniform(-s, s, (dim, 3 * gru)).astype(np.float32)
        weights["gru.wh"] = rng.uniform(-s, s, (gru, 3 * gru)).astype(np.float32)
        weights["gru.bi"] = np.zeros(3 * gru, dtype=np.float32)
        weights["gru.bh"] = np.zeros(3 * gru, dtype=np.float32)
        layers.append({"type": "gru", "hidden": gru, "wi": "gru.wi", "wh": "gru.wh", "bi": "gru.bi", "bh": "gru.bh"})
        dim = gru
    W = rng.standard_normal((dim, out_dim)).astype(np.float32) * np.float32(0.01)
    weights["out.w"] = W
    weights["out.b"] = np.zeros(out_dim, dtype=np.float32)
    layers.append({"type": "linear", "w": "out.w", "b": "out.b"})
    spec = {"format": FORMAT, "obs_version": OBS_VERSION, "input_dim": OBS_DIM, "layers": layers,
            "output": {"dim": out_dim, "squash": squash}, "meta": meta or {}}
    return Brain(spec, weights).validate()


def flat_params(brain: Brain, keys: list[str] | None = None) -> np.ndarray:
    keys = keys or sorted(brain.weights)
    return np.concatenate([brain.weights[k].ravel() for k in keys]).astype(np.float32)


def set_flat_params(brain: Brain, theta: np.ndarray, keys: list[str] | None = None) -> None:
    keys = keys or sorted(brain.weights)
    k = 0
    for name in keys:
        arr = brain.weights[name]
        n = arr.size
        brain.weights[name] = theta[k:k + n].reshape(arr.shape).astype(np.float32)
        k += n
