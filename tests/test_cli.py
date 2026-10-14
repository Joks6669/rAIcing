"""``paddock`` and ``stewards`` with Typer's CliRunner (local mode, and remote mode against a TestClient)."""
from __future__ import annotations

import gzip
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from conftest import FAST, env_for, fake_run_job, fake_validate
from raicing.api.app import create_app
from raicing.api.settings import ApiSettings
from raicing.brains.spec import mlp_brain
from raicing.cli.backend import RemoteBackend
from raicing.cli.paddock import app as paddock
from raicing.cli.stewards import app as stewards
from raicing.league import training
from raicing.league.service import ServiceOptions

runner = CliRunner()


def run(app, args, env, ok=True):
    res = runner.invoke(app, args, env=env, catch_exceptions=False)
    if ok:
        assert res.exit_code == 0, (args, res.output)
    return res


def js(app, args, env, ok=True):
    res = run(app, [*args, "--json"], env, ok)
    return json.loads(res.stdout)


def test_local_league_full_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(training, "run_job", fake_run_job)
    monkeypatch.setattr(training, "validate_config", fake_validate)
    env = env_for(tmp_path)
    init = js(stewards, ["init", "--name", "Ligue CLI", "--seed", "3", "--system-teams", "1", "--rounds", "1"], env)
    assert init["league"]["name"] == "Ligue CLI" and len(init["system_teams"]) == 1
    created = js(stewards, ["team", "create", "Astra Racing", "--color", "#2E86AB", "--director", "Astra 6",
                            "--driver", "Vega", "--driver", "Orion"], env)
    token = created["token"]
    assert token.startswith("rai_") and [d["name"] for d in created["team"]["drivers"]] == ["Vega", "Orion"]
    human = run(stewards, ["team", "create", "Opus Racing", "--color", "#C0392B"], env).stdout
    assert "Jeton (affiché une seule fois" in human
    assert js(stewards, ["season", "start", "1"], env)["status"] == "active"
    denv = {**env, "RAICING_TOKEN": token}

    status = js(paddock, ["status"], denv)
    assert status["id"] == "astra-racing" and status["next_round"]["number"] == 1
    assert "Écurie : Astra Racing" in run(paddock, ["status"], denv).stdout
    assert js(paddock, ["garage", "catalog"], denv)["available"] == 1000.0
    up = js(paddock, ["garage", "upgrade", "aero"], denv)
    assert up["garage"]["parts"]["aero"] == 1
    err = run(paddock, ["garage", "upgrade", "warp", "--json"], denv, ok=False)
    assert err.exit_code == 1 and "pièce inconnue" in json.loads(err.stdout)["error"]
    err = run(paddock, ["garage", "upgrade", "warp"], denv, ok=False)
    assert err.exit_code == 1 and "Erreur : pièce inconnue" in err.stderr
    assert "Moteur" in run(paddock, ["garage", "catalog"], denv).stdout
    regs = js(paddock, ["regs"], denv)
    assert regs["version"] == "S1"
    assert "Règlement de la saison 1" in run(paddock, ["regs"], denv).stdout

    # lab: template + local training (fake lab) for both drivers
    tpl = js(paddock, ["lab", "template", "--method", "es", "--out", str(tmp_path / "es.json")], denv)
    assert tpl["config"]["driver_id"] == "astra-racing-1" and tpl["config"]["method"] == "es"
    for k in (1, 2):
        cfg = {"driver_id": f"astra-racing-{k}", "method": "es", "steps": 2000, "activate": True, "seed": k}
        (tmp_path / f"cfg{k}.json").write_text(json.dumps(cfg))
        job = js(paddock, ["lab", "train", str(tmp_path / f"cfg{k}.json")], denv)
        assert job["status"] == "done" and job["steps_used"] == 2000
    assert len(js(paddock, ["lab", "jobs"], denv)) == 2
    brains = js(paddock, ["brains", "list"], denv)
    assert [b["active_for"] for b in brains] == [["astra-racing-1"], ["astra-racing-2"]]
    show = js(paddock, ["brains", "show", str(brains[0]["id"])], denv)
    assert show["architecture"]["hidden"] == [16]
    exp = js(paddock, ["brains", "export", str(brains[0]["id"]), str(tmp_path / "exported")], denv)
    assert (tmp_path / "exported" / "brain.json").exists() and exp["brain_id"] == brains[0]["id"]
    imp = run(paddock, ["brains", "import", str(tmp_path / "exported"), "--driver", "astra-racing-1", "--json"],
              denv, ok=False)
    assert imp.exit_code == 1 and json.loads(imp.stdout)["status"] == 403
    assert js(paddock, ["status"], denv)["quota"]["used"] == 4000

    # entry for the next round, then the stewards run it
    ent = js(paddock, ["entry", "submit", "next", "astra-racing-1", "--brain", str(brains[0]["id"]),
                       "--wing", "0.65", "--start", "soft"], denv)
    assert ent["setup"]["wing"] == 0.65 and ent["setup"]["start_compound"] == "soft"
    out = run(stewards, ["run-next"], env).stdout
    assert "courue" in out and "Podium" in out
    res = js(paddock, ["results"], denv)
    assert res["status"] == "done" and len(res["race"]["classification"]) == 4   # astra 2 + system 2 (opus forfeits)
    assert "Course :" in run(paddock, ["results", str(res["id"])], denv).stdout
    st = js(paddock, ["standings"], denv)
    assert st["drivers"][0]["points"] >= st["drivers"][-1]["points"] and len(st["teams"]) == 3
    assert "Classement écuries" in run(paddock, ["standings"], denv).stdout
    rep = js(paddock, ["replay", str(res["id"]), "--out", str(tmp_path / "r.json.gz")], denv)
    data = json.loads(gzip.decompress((tmp_path / "r.json.gz").read_bytes()))
    assert data["format"] == "raicing-replay/1" and rep["bytes"] > 1000
    assert "Manche 1" in run(paddock, ["calendar"], denv).stdout
    assert js(paddock, ["driver", "astra-racing-1"], denv)["results"][0]["round_id"] == res["id"]
    assert len(js(paddock, ["teams"], denv)) == 3

    # end of season
    closed = js(stewards, ["season", "close", "1"], env)
    assert closed["next_season"] == 2 and closed["change"]["parameter"]
    assert js(stewards, ["status"], env)["seasons"][1]["status"] == "planned"
    regs2 = js(paddock, ["regs", "--season", "2"], denv)
    assert regs2["version"] == "S2" and len(regs2["changes"]) == 1
    assert js(stewards, ["season", "start", "2"], env)["number"] == 2


def test_local_mode_errors(tmp_path):
    env = env_for(tmp_path)
    res = run(paddock, ["status", "--json"], env, ok=False)
    assert res.exit_code == 1 and "stewards init" in json.loads(res.stdout)["error"]
    js(stewards, ["init", "--seed", "1", "--system-teams", "0", "--rounds", "1"], env)
    res = run(paddock, ["status"], env, ok=False)
    assert res.exit_code == 1 and "RAICING_TOKEN" in res.stderr
    res = run(paddock, ["status", "--json"], {**env, "RAICING_TOKEN": "rai_faux"}, ok=False)
    assert json.loads(res.stdout)["status"] == 401
    assert json.loads(run(paddock, ["lab", "template", "--method", "nope", "--json"], env, ok=False).stdout)["error"]


def test_lab_template_methods(tmp_path):
    env = env_for(tmp_path)
    for method in ("ppo", "sac", "td3", "es", "bc"):
        out = js(paddock, ["lab", "template", "--method", method, "--driver", "x-1",
                           "--out", str(tmp_path / f"{method}.json")], env)
        cfg = json.loads((tmp_path / f"{method}.json").read_text())
        assert cfg == out["config"] and cfg["method"] == method and cfg["driver_id"] == "x-1"
        assert {"steps", "architecture", "hyperparams", "reward", "curriculum", "activate"} <= set(cfg)
        try:   # the real lab validator, when it is installed
            training.validate_config(cfg)
        except training.TrainingUnavailable:
            pass


@pytest.fixture
def remote(tmp_path, monkeypatch):
    app = create_app(ApiSettings(data_dir=tmp_path / "srv", database_url=f"sqlite:///{tmp_path / 'srv.db'}",
                                 admin_token="adm", options=ServiceOptions(**FAST)))
    client_headers: list[dict] = []

    def make_client(base_url, headers):
        client_headers.append(headers)
        return TestClient(app, base_url=base_url, headers=headers)
    monkeypatch.setattr(RemoteBackend, "_make_client", staticmethod(make_client))
    env = env_for(tmp_path, RAICING_MODE="remote", RAICING_API_URL="http://league.test", RAICING_ADMIN_TOKEN="adm")
    env["RAICING_LEAGUE_DIR"] = None
    return app, env


def test_remote_mode(remote, tmp_path):
    app, env = remote
    assert js(stewards, ["init", "--name", "Ligue distante", "--seed", "9", "--system-teams", "1",
                         "--rounds", "1"], env)["league"]["name"] == "Ligue distante"
    token = js(stewards, ["team", "create", "Astra Racing", "--color", "#2E86AB"], env)["token"]
    js(stewards, ["season", "start", "1"], env)
    denv = {**env, "RAICING_TOKEN": token}
    assert js(paddock, ["status"], denv)["id"] == "astra-racing"
    assert js(paddock, ["garage", "upgrade", "engine"], denv)["cost"] == 70.0
    err = run(paddock, ["garage", "upgrade", "warp", "--json"], denv, ok=False)
    assert json.loads(err.stdout) == {"error": "pièce inconnue : warp", "status": 400}
    bad = run(paddock, ["status", "--json"], {**env, "RAICING_TOKEN": "rai_faux"}, ok=False)
    assert json.loads(bad.stdout)["status"] == 401
    denied = run(stewards, ["team", "create", "Pirates", "--json"], {**env, "RAICING_ADMIN_TOKEN": token}, ok=False)
    assert json.loads(denied.stdout)["status"] == 403
    # a brain registered server-side, then activation / entry through the API
    from raicing.league.service import LeagueService
    with app.state.db.session() as s:
        svc = LeagueService(s, tmp_path / "srv", ServiceOptions(**FAST))
        bid = svc.register_brain("astra-racing", "astra-racing-1", mlp_brain([8], rng=np.random.default_rng(1)),
                                 method="es")["id"]
    assert js(paddock, ["brains", "activate", "astra-racing-1", str(bid)], denv)["active_brain"]["id"] == bid
    imp = run(paddock, ["brains", "import", str(tmp_path / "missing"), "--driver", "astra-racing-1", "--json"],
              denv, ok=False)
    assert "introuvable" in json.loads(imp.stdout)["error"]
    ent = js(paddock, ["entry", "submit", "next", "astra-racing-1", "--wing", "0.3"], denv)
    assert ent["brain_id"] == bid and ent["setup"]["wing"] == 0.3
    assert js(paddock, ["calendar"], denv)["rounds"][0]["status"] == "scheduled"
    assert js(stewards, ["status"], env)["current_season"] == 1
