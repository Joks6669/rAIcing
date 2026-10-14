"""Training jobs: configuration validation and the single entry point ``run_job``.

``run_job`` turns a training configuration (chosen by a team director, see docs/LAB.md) into a
new brain generation, consuming at most ``min(config["steps"], quota_remaining)`` interactions.
User-facing errors are raised as ``ValueError`` with French messages.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..brains.spec import ACTIVATIONS, Brain, BrainError
from ..config import Regulations
from ..sensors import OBS_DIM, OBS_VERSION
from ..world import ACT_DIM
from .envs import MODES, BrainResolver
from .evaluation import lab_track, run_solo
from .export import parse_mlp
from .quota import QuotaCounter, QuotaExceeded
from .rewards import RewardWeights

__all__ = ["run_job", "validate_config", "JobResult", "QuotaExceeded", "DEFAULT_CONFIG", "METHODS"]

LAB_VERSION = "1.0"
METHODS = ("ppo", "sac", "td3", "es", "bc")
DEFAULT_SQUASH = {"ppo": "clip", "sac": "tanh", "td3": "tanh", "es": "tanh", "bc": "clip"}
MAX_LAB_TRACK = 10_000_000          # lab seeds are 10_000_000 + k; validation seeds start at 20_000_000
EVAL_TRACKS = (0, 1)                # lab tracks used for the evaluation summary (never validation tracks)

DEFAULT_CONFIG: dict[str, Any] = {
    "driver_id": None,
    "method": "ppo",
    "steps": 2_000_000,
    "init_from": None,
    "architecture": {"hidden": [256, 256], "activation": "tanh", "gru": 0, "layernorm": False, "outputs": 11,
                     "squash": None},
    "hyperparams": {},
    "reward": RewardWeights().to_dict(),
    "curriculum": {
        "mode": "solo", "laps": 3, "opponents": ["pro", "prudent"], "rain_fraction": 0.2,
        "tyre_randomization": True, "track_rotation": 4000, "episode_decisions": 1500, "stall_time": 10.0,
        "learners_per_world": 1, "track_pool": 1000, "tracks": None,
    },
    "envs": 64,
    "seed": 0,
}


@dataclass
class JobResult:
    brain: Brain
    steps_used: int
    log: dict[str, Any] = field(default_factory=dict)
    eval: dict[str, Any] = field(default_factory=dict)


# ====================================================================== validation
def _hyperparam_keys(method: str) -> set[str]:
    if method in ("ppo", "sac", "td3"):
        from .algos.sb3 import method_defaults
        return set(method_defaults(method, False)) | set(method_defaults(method, True))
    if method == "es":
        from .algos.es import DEFAULTS
        return set(DEFAULTS)
    from .algos.bc import DEFAULTS as BC_DEFAULTS
    return set(BC_DEFAULTS)


def _is_int(x: Any) -> bool:
    return isinstance(x, (int, np.integer)) and not isinstance(x, bool)


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float, np.integer, np.floating)) and not isinstance(x, bool) and np.isfinite(x)


def _merge(defaults: dict[str, Any], given: Any, where: str) -> dict[str, Any]:
    if given is None:
        return copy.deepcopy(defaults)
    if not isinstance(given, dict):
        raise ValueError(f"« {where} » doit être un objet")
    unknown = sorted(set(given) - set(defaults))
    if unknown:
        raise ValueError(f"clé(s) inconnue(s) dans « {where} » : {', '.join(unknown)}")
    out = copy.deepcopy(defaults)
    out.update(copy.deepcopy(given))
    return out


def estimate_params(arch: dict[str, Any]) -> int:
    d = OBS_DIM
    n = 0
    for h in arch["hidden"]:
        n += d * h + h + (2 * h if arch["layernorm"] else 0)
        d = h
    if arch["gru"]:
        g = arch["gru"]
        n += d * 3 * g + g * 3 * g + 6 * g
        d = g
    return n + d * arch["outputs"] + arch["outputs"]


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a complete, normalised copy of ``config`` (defaults filled) or raise ``ValueError``."""
    if not isinstance(config, dict):
        raise ValueError("la configuration doit être un objet JSON")
    top_unknown = sorted(set(config) - set(DEFAULT_CONFIG))
    if top_unknown:
        raise ValueError(f"clé(s) inconnue(s) dans la configuration : {', '.join(top_unknown)}")
    cfg: dict[str, Any] = {}
    cfg["driver_id"] = config.get("driver_id")
    if cfg["driver_id"] is not None and not isinstance(cfg["driver_id"], str):
        raise ValueError("« driver_id » doit être une chaîne")
    method = str(config.get("method", DEFAULT_CONFIG["method"])).lower()
    if method not in METHODS:
        raise ValueError(f"méthode inconnue : {method!r} (attendu : {', '.join(METHODS)})")
    cfg["method"] = method
    steps = config.get("steps", DEFAULT_CONFIG["steps"])
    if not _is_int(steps) or steps <= 0:
        raise ValueError("« steps » doit être un entier strictement positif")
    cfg["steps"] = int(steps)
    init_from = config.get("init_from")
    if init_from is not None and not isinstance(init_from, (str, int)):
        raise ValueError("« init_from » doit être un identifiant de génération (chaîne ou entier) ou null")
    cfg["init_from"] = init_from

    # ---- architecture
    arch = _merge(DEFAULT_CONFIG["architecture"], config.get("architecture"), "architecture")
    hidden = arch["hidden"]
    if not isinstance(hidden, (list, tuple)) or not 1 <= len(hidden) <= 8 or \
            not all(_is_int(h) and 1 <= h <= 2048 for h in hidden):
        raise ValueError("« architecture.hidden » doit être une liste de 1 à 8 tailles entières entre 1 et 2048")
    arch["hidden"] = [int(h) for h in hidden]
    if arch["activation"] not in ACTIVATIONS:
        raise ValueError(f"activation inconnue : {arch['activation']!r} (attendu : {', '.join(sorted(ACTIVATIONS))})")
    if not _is_int(arch["gru"]) or not 0 <= arch["gru"] <= 1024:
        raise ValueError("« architecture.gru » doit être un entier entre 0 (pas de mémoire) et 1024")
    arch["gru"] = int(arch["gru"])
    if not isinstance(arch["layernorm"], bool):
        raise ValueError("« architecture.layernorm » doit être un booléen")
    if not _is_int(arch["outputs"]) or not 2 <= arch["outputs"] <= ACT_DIM:
        raise ValueError(f"« architecture.outputs » doit être un entier entre 2 et {ACT_DIM}")
    arch["outputs"] = int(arch["outputs"])
    if arch["squash"] is None:
        arch["squash"] = DEFAULT_SQUASH[method]
    if arch["squash"] not in ("tanh", "clip", "none"):
        raise ValueError("« architecture.squash » doit valoir tanh, clip ou none")
    if method == "ppo" and arch["squash"] != "clip":
        raise ValueError("ppo exporte une sortie « clip » : architecture.squash doit valoir clip")
    if method in ("sac", "td3") and arch["squash"] != "tanh":
        raise ValueError(f"{method} exporte une sortie « tanh » : architecture.squash doit valoir tanh")
    if arch["gru"] and method != "es":
        raise ValueError(f"les couches GRU ne sont prises en charge que par la méthode es (pas {method})")
    if arch["layernorm"] and method not in ("es", "bc"):
        raise ValueError(f"layernorm n'est pris en charge que par es et bc (pas {method})")
    if estimate_params(arch) > 2_000_000:
        raise ValueError(f"architecture trop grande : {estimate_params(arch)} paramètres (maximum 2 000 000)")
    if len(arch["hidden"]) * (3 if arch["layernorm"] else 2) + 2 > 48:
        raise ValueError("architecture trop profonde (48 couches au maximum)")
    cfg["architecture"] = arch

    # ---- hyperparameters
    hp = config.get("hyperparams") or {}
    if not isinstance(hp, dict):
        raise ValueError("« hyperparams » doit être un objet")
    unknown = sorted(set(hp) - _hyperparam_keys(method))
    if unknown:
        raise ValueError(f"hyperparamètre(s) inconnu(s) pour {method} : {', '.join(unknown)}")
    for k, v in hp.items():
        if v is not None and not isinstance(v, (int, float, str, bool)):
            raise ValueError(f"hyperparamètre « {k} » : valeur scalaire attendue")
        if isinstance(v, float) and not np.isfinite(v):
            raise ValueError(f"hyperparamètre « {k} » : valeur non finie")
    if method == "es":
        pop = hp.get("population", 64)
        if not _is_int(pop) or pop < 2 or pop % 2:
            raise ValueError("es : « population » doit être un entier pair ≥ 2")
        if "sigma" in hp and not (_is_num(hp["sigma"]) and hp["sigma"] > 0):
            raise ValueError("es : « sigma » doit être strictement positif")
    if method == "bc" and "teacher" in hp:
        from ..drivers.reference import PERSONALITIES
        if hp["teacher"] not in PERSONALITIES:
            raise ValueError(f"bc : professeur inconnu {hp['teacher']!r} (attendu : {', '.join(PERSONALITIES)})")
    for k in ("lr", "batch_size", "n_steps", "gamma"):
        if k in hp and not (_is_num(hp[k]) and hp[k] > 0):
            raise ValueError(f"hyperparamètre « {k} » : nombre strictement positif attendu")
    if "gamma" in hp and not hp["gamma"] <= 1.0:
        raise ValueError("« gamma » doit être dans ]0, 1]")
    if "obs_norm" in hp and hp["obs_norm"] not in ("update", "freeze"):
        raise ValueError("« obs_norm » doit valoir update ou freeze")
    cfg["hyperparams"] = dict(hp)

    # ---- reward
    reward = _merge(DEFAULT_CONFIG["reward"], config.get("reward"), "reward")
    for k, v in reward.items():
        if not _is_num(v):
            raise ValueError(f"poids de récompense « {k} » : nombre attendu")
        reward[k] = float(v)
    cfg["reward"] = reward

    # ---- curriculum
    cur = _merge(DEFAULT_CONFIG["curriculum"], config.get("curriculum"), "curriculum")
    if cur["mode"] not in MODES:
        raise ValueError(f"mode de curriculum inconnu : {cur['mode']!r} (attendu : {', '.join(MODES)})")
    if not _is_int(cur["laps"]) or not 1 <= cur["laps"] <= 30:
        raise ValueError("« curriculum.laps » doit être un entier entre 1 et 30")
    opps = cur["opponents"]
    if not isinstance(opps, list) or len(opps) > 21 or not all(isinstance(o, (str, int)) and str(o) for o in opps):
        raise ValueError("« curriculum.opponents » doit être une liste (au plus 21) de personnalités ou d'ids de cerveaux")
    cur["opponents"] = [str(o) for o in opps]
    if not _is_num(cur["rain_fraction"]) or not 0.0 <= cur["rain_fraction"] <= 1.0:
        raise ValueError("« curriculum.rain_fraction » doit être entre 0 et 1")
    if not isinstance(cur["tyre_randomization"], bool):
        raise ValueError("« curriculum.tyre_randomization » doit être un booléen")
    if not _is_int(cur["track_rotation"]) or cur["track_rotation"] < 100:
        raise ValueError("« curriculum.track_rotation » doit être un entier ≥ 100")
    if not _is_int(cur["episode_decisions"]) or cur["episode_decisions"] < 20:
        raise ValueError("« curriculum.episode_decisions » doit être un entier ≥ 20")
    if not _is_num(cur["stall_time"]) or not 1.0 <= cur["stall_time"] <= 60.0:
        raise ValueError("« curriculum.stall_time » doit être entre 1 et 60 secondes")
    if cur["learners_per_world"] not in (1, 2):
        raise ValueError("« curriculum.learners_per_world » doit valoir 1 ou 2")
    if not _is_int(cur["track_pool"]) or not 1 <= cur["track_pool"] <= MAX_LAB_TRACK:
        raise ValueError(f"« curriculum.track_pool » doit être entre 1 et {MAX_LAB_TRACK}")
    if cur["tracks"] is not None:
        if not isinstance(cur["tracks"], list) or not cur["tracks"] or \
                not all(_is_int(k) and 0 <= k < MAX_LAB_TRACK for k in cur["tracks"]):
            raise ValueError("« curriculum.tracks » doit être une liste d'indices de circuits du laboratoire "
                             f"(0 ≤ k < {MAX_LAB_TRACK}) — les circuits de validation sont interdits")
        cur["tracks"] = [int(k) for k in cur["tracks"]]
    cur["rain_fraction"] = float(cur["rain_fraction"])
    cur["stall_time"] = float(cur["stall_time"])
    cfg["curriculum"] = cur

    envs = config.get("envs", DEFAULT_CONFIG["envs"])
    if not _is_int(envs) or not 1 <= envs <= 1024:
        raise ValueError("« envs » doit être un entier entre 1 et 1024")
    cfg["envs"] = int(envs)
    seed = config.get("seed", DEFAULT_CONFIG["seed"])
    if not _is_int(seed) or seed < 0:
        raise ValueError("« seed » doit être un entier positif")
    cfg["seed"] = int(seed)
    return cfg


def config_hash(cfg: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ====================================================================== compatibility
def check_init_compatible(cfg: dict[str, Any], brain: Brain) -> None:
    """Raise a clear ``ValueError`` if ``brain`` cannot seed training with ``cfg``."""
    try:
        brain.validate()
        desc = parse_mlp(brain)
    except BrainError as e:
        raise ValueError(f"init_from : cerveau de départ inutilisable ({e})") from e
    arch = cfg["architecture"]
    diffs = []
    for k in ("hidden", "activation", "gru", "layernorm", "outputs", "squash"):
        if desc[k] != arch[k]:
            diffs.append(f"{k} : cerveau {desc[k]!r} ≠ configuration {arch[k]!r}")
    if diffs:
        raise ValueError("init_from : architecture incompatible — " + " ; ".join(diffs))


# ====================================================================== evaluation
_PRO_CACHE: dict[tuple, float | None] = {}


def evaluation_summary(brain: Brain, regs: Regulations, tracks: tuple[int, ...] = EVAL_TRACKS,
                       laps: int = 2) -> dict[str, Any]:
    """``run_solo`` on lab tracks (never validation tracks), with the reference "pro" for scale."""
    out: dict[str, Any] = {"tracks": [], "laps": laps}
    for k in tracks:
        tr = lab_track(k)
        res = run_solo(brain, tr, regs, laps=laps)
        key = (k, laps, regs.version, json.dumps(regs.to_dict(), sort_keys=True, default=str))
        if key not in _PRO_CACHE:
            _PRO_CACHE[key] = run_solo("pro", tr, regs, laps=laps).best
        pro = _PRO_CACHE[key]
        d = res.to_dict()
        d["best"] = round(float(d["best"]), 3) if d["best"] is not None else None
        d.update({"track": f"lab:{k}", "name": tr.name, "pro_best": round(pro, 3) if pro else None,
                  "laps_done": len(res.lap_times)})
        if d["best"] is not None and pro:
            d["ratio_to_pro"] = round(d["best"] / pro, 4)
        out["tracks"].append(d)
    done = [t for t in out["tracks"] if t["laps_done"] >= laps]
    out["completed"] = len(done)
    out["crashes"] = sum(1 for t in out["tracks"] if t["crashed"])
    return jsonable(out)


def jsonable(obj: Any) -> Any:
    """Recursively convert numpy scalars/arrays (and tuples) into plain JSON types."""
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return jsonable(obj.tolist())
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    return obj


def _short_curve(curve: list[dict[str, Any]], n: int = 40) -> list[dict[str, Any]]:
    if len(curve) <= n:
        return curve
    idx = np.unique(np.linspace(0, len(curve) - 1, n).round().astype(int))
    return [curve[i] for i in idx]


# ====================================================================== entry point
def run_job(config: dict[str, Any], regs: Regulations | None, init_brain: Brain | None, quota_remaining: int,
            workdir: Path | str, progress: Callable[[dict], None] | None = None,
            should_stop: Callable[[], bool] | None = None, brain_resolver: BrainResolver | None = None,
            evaluate: bool = True, verbose: bool = False) -> JobResult:
    """Train a new brain generation. See docs/LAB.md.

    ``brain_resolver`` maps the frozen-brain ids of ``curriculum.opponents`` to brains (traffic mode).
    ``steps_used`` never exceeds ``min(config["steps"], quota_remaining)``."""
    cfg = validate_config(config)
    regs = regs or Regulations()
    if cfg["init_from"] is not None and init_brain is None:
        raise ValueError(f"init_from={cfg['init_from']!r} : le cerveau de départ n'a pas été fourni")
    if init_brain is not None:
        check_init_compatible(cfg, init_brain)
    if estimate_params(cfg["architecture"]) > regs.training.max_brain_params:
        raise ValueError(f"architecture trop grande pour le règlement ({regs.training.max_brain_params} paramètres max.)")
    limit = min(cfg["steps"], int(quota_remaining))
    if limit <= 0:
        raise QuotaExceeded("quota d'interactions épuisé : aucun entraînement possible")
    quota = QuotaCounter(limit)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    log_path = workdir / "train.log"
    log_file = log_path.open("a", encoding="utf-8")
    t_start = time.time()

    def log_fn(msg: str) -> None:
        line = f"[{time.time() - t_start:8.1f}s] {msg}"
        log_file.write(line + "\n")
        log_file.flush()
        if verbose:
            print(line, flush=True)

    deadline = t_start + float(regs.training.max_job_wallclock)

    def stop() -> bool:
        return time.time() > deadline or bool(should_stop and should_stop())

    def prog(d: dict) -> None:
        if progress is not None:
            progress({"method": cfg["method"], "budget": limit, **d})

    import torch
    torch.set_num_threads(2)
    log_fn(f"job {cfg['method']} budget {limit} config {config_hash(cfg)}")
    method = cfg["method"]
    try:
        if method in ("ppo", "sac", "td3"):
            from .algos.sb3 import train_sb3
            brain, log = train_sb3(method, cfg, regs, init_brain, quota, prog, stop, brain_resolver, log_fn)
        elif method == "es":
            from .algos.es import train_es
            brain, log = train_es(cfg, regs, init_brain, quota, prog, stop, brain_resolver, log_fn)
        else:
            from .algos.bc import train_bc
            brain, log = train_bc(cfg, regs, init_brain, quota, prog, stop, log_fn)
    finally:
        log_file.close()
    brain.validate(regs.training.max_brain_params, regs.training.max_brain_layers)
    duration = time.time() - t_start
    log["duration"] = round(duration, 1)
    log["steps_used"] = quota.used
    log["throughput"] = round(quota.used / max(duration, 1e-9), 1)
    ev = evaluation_summary(brain, regs) if evaluate else {}
    parent = cfg["init_from"]
    if parent is None and init_brain is not None:
        parent = init_brain.meta.get("id") or init_brain.meta.get("config_hash")
    brain.spec["meta"] = jsonable({
        **{k: v for k, v in brain.meta.items() if k not in ("training_curve",)},
        "driver_id": cfg["driver_id"],
        "method": method,
        "steps": quota.used,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "parent": parent,
        "config_hash": config_hash(cfg),
        "config": cfg,
        "architecture": cfg["architecture"],
        "training_curve": _short_curve(log.get("curve", [])),
        "eval": ev,
        "regs_version": regs.version,
        "obs_version": OBS_VERSION,
        "lab_version": LAB_VERSION,
        "duration_s": round(duration, 1),
    })
    ev = brain.spec["meta"]["eval"]
    log = jsonable(log)
    brain.save(workdir)                      # workdir/brain.json + workdir/weights.npz
    (workdir / "log.json").write_text(json.dumps({"log": log, "eval": ev}, indent=1, default=str, ensure_ascii=False))
    return JobResult(brain=brain, steps_used=quota.used, log=log, eval=ev)
