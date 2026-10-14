"""Shared fixtures: isolated environment, fast service options, fake training lab."""
from __future__ import annotations

import types
from pathlib import Path

import numpy as np
import pytest

from raicing.brains.spec import mlp_brain
from raicing.league import training
from raicing.league.db import Database, default_database_url
from raicing.league.service import LeagueService, ServiceOptions

# 1-lap races, no regulation bench, near-instant live broadcast
FAST = dict(laps_override=1, run_bench=False, live=True, live_speed=1e6, embargo=True)

ENV_VARS = ("DATABASE_URL", "RAICING_MODE", "RAICING_LEAGUE_DIR", "RAICING_API_URL", "RAICING_TOKEN", "RAICING_TEAM",
            "RAICING_ADMIN_TOKEN", "RAICING_DATA_DIR", "RAICING_TEST_LAPS", "RAICING_RUN_BENCH", "RAICING_LIVE",
            "RAICING_LIVE_SPEED", "RAICING_EMBARGO", "RAICING_RATING_QUICK", "RAICING_QUALI_TIME_LIMIT",
            "RAICING_CONFIG")


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RAICING_CONFIG", str(tmp_path / "no-config.json"))


def fake_validate(config: dict) -> dict:
    """Minimal stand-in for ``raicing.lab.jobs.validate_config``."""
    training.basic_checks(config)
    cfg = training.lab_config(config)
    cfg.setdefault("method", "es")
    cfg.setdefault("steps", 1000)
    cfg.setdefault("init_from", None)
    cfg.setdefault("curriculum", {"mode": "solo", "opponents": []})
    if cfg["method"] not in training.METHODS:
        raise ValueError(f"méthode inconnue : {cfg['method']}")
    if not isinstance(cfg["steps"], int) or cfg["steps"] <= 0:
        raise ValueError("« steps » doit être un entier strictement positif")
    cfg["activate"] = bool(config.get("activate", False))
    return cfg


def fake_run_job(config, regs, init_brain, quota_remaining, workdir, progress=None, should_stop=None, **_kw):
    """Stand-in for ``run_job``: consumes the budget instantly and returns a small random brain."""
    steps = min(int(config["steps"]), int(quota_remaining))
    if progress is not None:
        progress({"steps": steps // 2, "mean_reward": 1.5})
    Path(workdir).mkdir(parents=True, exist_ok=True)
    brain = mlp_brain([16], rng=np.random.default_rng(int(config.get("seed", 0)) + 7))
    return types.SimpleNamespace(brain=brain, steps_used=steps, log={"curve": []}, eval={"completed": 0})


@pytest.fixture
def fake_lab(monkeypatch):
    monkeypatch.setattr(training, "run_job", fake_run_job)
    monkeypatch.setattr(training, "validate_config", fake_validate)
    return training


@pytest.fixture
def db(tmp_path):
    database = Database(default_database_url(tmp_path), tmp_path)
    yield database
    database.dispose()


@pytest.fixture
def make_service(db, tmp_path):
    sessions = []

    def make(**opts) -> LeagueService:
        s = db.Session()
        sessions.append(s)
        return LeagueService(s, tmp_path, ServiceOptions(**{**FAST, **opts}))
    yield make
    for s in sessions:
        s.close()


# ---------------------------------------------------------------------- helpers
def setup_league(svc: LeagueService, n_rounds: int = 1, total_teams: int = 3, seed: int = 42) -> dict[str, str]:
    """League + teams ``astra-racing`` and ``opus-racing`` + system teams up to ``total_teams``."""
    svc.create_league("Ligue de test", seed=seed, n_rounds=n_rounds)
    tokens = {}
    for name, color, director in (("Astra Racing", "#2E86AB", "Astra 6"), ("Opus Racing", "#C0392B", "Opus 5.5")):
        out = svc.create_team(name, color, director)
        tokens[out["team"]["id"]] = out["token"]
    svc.ensure_system_teams(total_teams)
    return tokens


def give_brain(svc: LeagueService, team: str, k: int, activate: bool = True) -> int:
    brain = mlp_brain([16], rng=np.random.default_rng(100 + k + len(team)))
    return svc.register_brain(team, f"{team}-{k}", brain, method="es", activate=activate)["id"]


def env_for(tmp_path: Path, **extra: str) -> dict[str, str | None]:
    """Environment for CLI tests (local league in ``tmp_path / 'league'``)."""
    env: dict[str, str | None] = {v: None for v in ENV_VARS}
    env.update({"RAICING_CONFIG": str(tmp_path / "no-config.json"), "RAICING_MODE": "local",
                "RAICING_LEAGUE_DIR": str(tmp_path / "league"), "RAICING_TEST_LAPS": "1", "RAICING_RUN_BENCH": "0"})
    env.update(extra)
    return env

