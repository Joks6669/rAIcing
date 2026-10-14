"""Thin adapter to the training lab (``raicing.lab.jobs``, see docs/LAB.md).

The lab is an optional, heavy dependency (PyTorch, stable-baselines3...). It is imported lazily
so that the league services, the API and the CLI work without it; calling ``run_job`` or
``validate_config`` without the lab raises :class:`TrainingUnavailable` with a clear message.
Tests monkeypatch ``run_job`` / ``validate_config`` in this module.
"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

METHODS = ("ppo", "sac", "td3", "es", "bc")
LEAGUE_KEYS = ("activate", "kind")   # handled by the league, never passed to the lab


class TrainingUnavailable(RuntimeError):
    """The lab module cannot be imported (missing dependencies or not installed)."""


def lab_module() -> ModuleType:
    try:
        return importlib.import_module("raicing.lab.jobs")
    except Exception as exc:  # ImportError, or an error raised while importing optional deps
        raise TrainingUnavailable(
            "Le laboratoire d'entraînement n'est pas disponible sur cette installation "
            f"(import de raicing.lab.jobs impossible : {exc}). "
            "Installez les dépendances avec `pip install -e .[lab]`.") from exc


def lab_config(config: dict[str, Any]) -> dict[str, Any]:
    """The configuration as the lab sees it (league-only keys removed)."""
    return {k: v for k, v in config.items() if k not in LEAGUE_KEYS}


def basic_checks(config: dict[str, Any]) -> None:
    """League-level checks (the lab validates everything else)."""
    if not isinstance(config, dict):
        raise ValueError("la configuration doit être un objet JSON")
    if not isinstance(config.get("driver_id"), str) or not config["driver_id"]:
        raise ValueError("« driver_id » manquant : le pilote à entraîner est obligatoire")
    if "activate" in config and not isinstance(config["activate"], bool):
        raise ValueError("« activate » doit être un booléen")


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise a training configuration with the lab's validator.

    League-only keys (``activate``) are kept aside and restored in the returned copy."""
    basic_checks(config)
    out = lab_module().validate_config(lab_config(config))
    out = dict(out) if isinstance(out, dict) else lab_config(config)
    out["activate"] = bool(config.get("activate", False))
    return out


def run_job(**kwargs: Any) -> Any:
    """Forward to ``raicing.lab.jobs.run_job`` (keyword arguments of docs/LAB.md)."""
    return lab_module().run_job(**kwargs)

