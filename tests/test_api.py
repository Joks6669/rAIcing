"""HTTP API (FastAPI TestClient): public shapes, authentication, team actions, admin, replays, live."""
from __future__ import annotations

import base64
import gzip
import json
import time
import types

import pytest
from fastapi.testclient import TestClient

from conftest import FAST, fake_run_job, fake_validate
from raicing.api.app import create_app
from raicing.api.settings import ApiSettings
from raicing.brains.spec import mlp_brain
from raicing.league import training
from raicing.league.live import STATE_FILE, LivePublisher
from raicing.league.service import LeagueService, ServiceOptions

ADMIN = "admin-secret-token"
AH = {"Authorization": f"Bearer {ADMIN}"}


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    d = tmp_path_factory.mktemp("api")
    settings = ApiSettings(data_dir=d, database_url=f"sqlite:///{d / 'league.db'}", admin_token=ADMIN,
                           options=ServiceOptions(**FAST))
    app = create_app(settings)
    client = TestClient(app)
    r = client.post("/api/admin/league", json={"name": "API League", "seed": 5, "rounds": 2, "system_teams": 1},
                    headers=AH)
    assert r.status_code == 201, r.text
    tokens = {}
    for name in ("Astra Racing", "Opus Racing"):
        r = client.post("/api/admin/teams", json={"name": name, "color": "#2E86AB", "director": name[:4]}, headers=AH)
        assert r.status_code == 201, r.text
        tokens[r.json()["team"]["id"]] = r.json()["token"]
    assert client.post("/api/admin/seasons/1/start", headers=AH).status_code == 200
    with app.state.db.session() as s:
        svc = LeagueService(s, d, settings.options)
        brains = [svc.register_brain("astra-racing", f"astra-racing-{k}", mlp_brain([16]), method="es",
                                     activate=True)["id"] for k in (1, 2)]
    rounds = client.get("/api/seasons/1").json()["rounds"]
    run = client.post(f"/api/admin/rounds/{rounds[0]['id']}/run", headers=AH)
    assert run.status_code == 200, run.text
    return types.SimpleNamespace(client=client, app=app, dir=d, tokens=tokens, brains=brains,
                                 rounds=[r["id"] for r in rounds])


def team_h(api, team="astra-racing"):
    return {"Authorization": f"Bearer {api.tokens[team]}"}


# ---------------------------------------------------------------------- public
def test_public_shapes(api):
    c = api.client
    assert c.get("/healthz").json() == {"ok": True, "league": True}
    lg = c.get("/api/league").json()
    assert lg["name"] == "API League" and lg["current_season"] == 1
    assert lg["seasons"] == [{"number": 1, "status": "active", "rounds": 2, "rounds_done": 1}]
    season = c.get("/api/seasons/1").json()
    assert {"number", "status", "budget", "quota", "regulation_change", "rounds"} <= set(season)
    done, nxt = season["rounds"]
    assert {"id", "number", "status", "track_name", "track_length", "laps", "category", "rain_probability", "winner",
            "pole", "date"} <= set(done)
    assert done["status"] == "done" and done["track_name"] and set(done["winner"]) == {
        "driver_id", "driver", "team_id", "team", "color"}
    assert nxt["track_name"] is None and nxt["winner"] is None and nxt["track_length"] % 100 == 0
    st = c.get("/api/seasons/1/standings").json()
    d0, t0 = st["drivers"][0], st["teams"][0]
    assert {"pos", "driver_id", "driver", "team_id", "team", "color", "points", "wins", "podiums", "dnf",
            "by_round"} <= set(d0) and len(d0["by_round"]) == 2 and d0["by_round"][1] is None
    assert {"pos", "team_id", "team", "color", "points", "penalties", "by_round"} <= set(t0)
    rd = c.get(f"/api/rounds/{api.rounds[0]}").json()
    assert {"id", "season", "number", "status", "laps", "track", "weather", "quali", "race", "entries",
            "has_replay"} <= set(rd)
    assert set(rd["track"]) == {"name", "length", "seed", "corners", "category"}
    assert {"classification", "fastest_lap", "incidents", "team_penalties"} <= set(rd["race"])
    row = rd["race"]["classification"][0]
    assert {"pos", "driver", "team", "status", "reason", "laps", "time", "gap", "best_lap", "lap_times", "pits",
            "points", "penalty"} <= set(row)
    assert {"pos", "driver", "team", "time", "gap", "status", "reason", "compound"} <= set(
        rd["quali"]["classification"][0])
    entry = next(e for e in rd["entries"] if e["driver_id"] == "astra-racing-1")
    assert entry["entry_id"] == "astra-racing#1" and entry["brain"]["id"] == api.brains[0]
    assert set(entry["setup"]) == {"wing", "brake_bias", "start_compound", "quali_compound"}
    assert rd["has_replay"] == {"quali": True, "race": True}
    unraced = c.get(f"/api/rounds/{api.rounds[1]}").json()
    assert unraced["track"]["seed"] is None and unraced["race"] is None
    teams = c.get("/api/teams").json()
    assert len(teams) == 3
    team = c.get("/api/teams/astra-racing").json()
    assert {"id", "name", "color", "director", "is_system", "drivers", "garage", "results", "quota"} <= set(team)
    assert {"season", "budget", "spent", "available", "parts", "technologies", "capacity"} <= set(team["garage"])
    assert team["drivers"][0]["active_brain"] == {"id": api.brains[0], "generation": 1}
    assert team["quota"] == {"season": 1, "used": 0, "total": 40_000_000}
    drv = c.get("/api/drivers/astra-racing-1").json()
    assert {"id", "name", "team_id", "team", "color", "number", "active_brain", "rating", "generations",
            "results"} <= set(drv)
    assert drv["results"][0]["round_id"] == api.rounds[0]
    regs = c.get("/api/regulations/1").json()
    assert regs["version"] == "S1" and regs["changes"] == [] and "car" in regs["regulations"]
    assert c.get("/api/live").json() == {"live": False}
    assert c.get("/api/rounds/9999").status_code == 404
    assert c.get("/api/teams/nope").status_code == 404
    # secrets never leak
    blob = json.dumps([lg, season, st, rd, teams, drv, regs])
    assert "token_hash" not in blob and "secret" not in blob and api.tokens["astra-racing"] not in blob


def test_replay_endpoint(api):
    c = api.client
    r = c.get(f"/api/rounds/{api.rounds[0]}/replay")
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    rep = r.json()
    assert rep["format"] == "raicing-replay/1" and rep["kind"] == "race" and rep["frames"]
    assert len(rep["frames"][0][1]) == len(rep["frame_fields"]) == 13
    plain = c.get(f"/api/rounds/{api.rounds[0]}/replay?session=quali", headers={"Accept-Encoding": "identity"})
    assert plain.status_code == 200 and "content-encoding" not in plain.headers and plain.json()["kind"] == "quali"
    assert c.get(f"/api/rounds/{api.rounds[1]}/replay").status_code == 404
    assert c.get(f"/api/rounds/{api.rounds[0]}/replay?session=fp1").status_code == 400


# ---------------------------------------------------------------------- auth
def test_auth_failures(api):
    c = api.client
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/me", headers={"Authorization": "Bearer rai_nope"}).status_code == 401
    assert c.get("/api/me", headers={"Authorization": "Basic abc"}).status_code == 401
    assert c.post("/api/admin/teams", json={"name": "X"}).status_code == 401
    assert c.post("/api/admin/teams", json={"name": "X"}, headers=team_h(api)).status_code == 403
    assert c.post("/api/admin/rounds/1/run", headers={"Authorization": "Bearer wrong"}).status_code == 403
    # driver of another team, brain of another team
    assert c.post("/api/drivers/opus-racing-1/activate", json={"brain_id": api.brains[0]},
                  headers=team_h(api)).status_code == 403
    assert c.post("/api/drivers/opus-racing-1/activate", json={"brain_id": api.brains[0]},
                  headers=team_h(api, "opus-racing")).status_code == 403
    assert c.get(f"/api/brains/{api.brains[0]}", headers=team_h(api, "opus-racing")).status_code == 403


def test_admin_disabled_without_token(tmp_path):
    app = create_app(ApiSettings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'l.db'}",
                                 admin_token=None, options=ServiceOptions(**FAST)))
    c = TestClient(app)
    r = c.post("/api/admin/league", json={"name": "x"}, headers={"Authorization": "Bearer anything"})
    assert r.status_code == 403 and "RAICING_ADMIN_TOKEN" in r.json()["detail"]
    assert c.get("/api/league").status_code == 404


# ---------------------------------------------------------------------- team space
def test_team_actions(api, monkeypatch):
    c = api.client
    h = team_h(api)
    me = c.get("/api/me", headers=h).json()
    assert me["id"] == "astra-racing" and me["quota"]["remaining"] == 40_000_000
    assert me["next_round"]["id"] == api.rounds[1]
    cat = c.get("/api/garage/catalog", headers=h).json()
    assert len(cat["parts"]) == 7 and len(cat["technologies"]) == 6 and cat["season"] == 1
    r = c.post("/api/garage/upgrade", json={"part": "suspension"}, headers=h)
    assert r.status_code == 200 and r.json()["garage"]["parts"]["suspension"] >= 1
    r = c.post("/api/garage/upgrade", json={"part": "warp"}, headers=h)
    assert r.status_code == 400 and "pièce inconnue" in r.json()["detail"]
    assert c.post("/api/garage/revise", json={"part": "aero"}, headers=h).status_code == 400
    assert c.post("/api/garage/research", json={"technology": "composites"}, headers=h).status_code == 200
    assert c.post("/api/garage/upgrade", json={}, headers=h).status_code == 422
    brains = c.get("/api/brains", headers=h).json()
    assert [b["id"] for b in brains] == api.brains
    pkg = c.get(f"/api/brains/{api.brains[0]}/package", headers=h)
    assert pkg.status_code == 200 and pkg.headers["content-type"] == "application/zip"
    imp = c.post("/api/brains/import", json={"driver_id": "astra-racing-1",
                                             "package": base64.b64encode(pkg.content).decode()}, headers=h)
    assert imp.status_code == 403
    assert c.post("/api/brains/import", json={"driver_id": "astra-racing-1", "package": "%%%"},
                  headers=h).status_code == 400
    # entries
    rid = api.rounds[1]
    e = c.post(f"/api/rounds/{rid}/entries", json={"driver_id": "astra-racing-2", "brain_id": api.brains[1],
                                                   "setup": {"wing": 0.4, "quali_compound": "medium"}}, headers=h)
    assert e.status_code == 201 and e.json()["setup"]["wing"] == 0.4
    bad = c.post(f"/api/rounds/{rid}/entries", json={"driver_id": "astra-racing-2", "brain_id": api.brains[0]},
                 headers=h)
    assert bad.status_code == 409
    done = c.post(f"/api/rounds/{api.rounds[0]}/entries", json={"driver_id": "astra-racing-1"}, headers=h)
    assert done.status_code == 409
    act = c.post("/api/drivers/astra-racing-2/activate", json={"brain_id": api.brains[1]}, headers=h)
    assert act.status_code == 200 and act.json()["active_brain"]["id"] == api.brains[1]
    # lab jobs with the fake lab
    monkeypatch.setattr(training, "run_job", fake_run_job)
    monkeypatch.setattr(training, "validate_config", fake_validate)
    j = c.post("/api/lab/jobs", json={"driver_id": "astra-racing-1", "method": "es", "steps": 1000}, headers=h)
    assert j.status_code == 201 and j.json()["status"] == "queued"
    jid = j.json()["id"]
    assert c.post("/api/lab/jobs", json={"method": "es"}, headers=h).status_code == 400
    assert [x["id"] for x in c.get("/api/lab/jobs", headers=h).json()][0] == jid
    assert c.get(f"/api/lab/jobs/{jid}", headers=h).json()["steps_requested"] == 1000
    assert c.get(f"/api/lab/jobs/{jid}", headers=team_h(api, "opus-racing")).status_code == 403
    assert c.delete(f"/api/lab/jobs/{jid}", headers=h).json()["status"] == "cancelled"
    assert c.delete(f"/api/lab/jobs/{jid}", headers=h).status_code == 409
    rating = c.post(f"/api/brains/{api.brains[0]}/rating", headers=h)
    assert rating.status_code == 202 and rating.json()["kind"] == "rating"


# ---------------------------------------------------------------------- live websocket
def test_ws_live_idle_and_stream(tmp_path):
    app = create_app(ApiSettings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'l.db'}",
                                 admin_token=ADMIN, options=ServiceOptions(**FAST), live_poll=0.02))
    c = TestClient(app)
    with c.websocket_connect("/ws/live") as ws:
        assert ws.receive_json() == {"type": "idle", "live": False}
    pub = LivePublisher(tmp_path / "live", speed=1.0)
    pub.start(5, 1, 1, {"format": "raicing-replay/1", "cars": [{"entry_id": "a#1"}]})
    frames = [[round(0.2 * k, 1), [float(k), 0.0]] for k in range(1, 5)]
    pub.flush(frames, [], [{"t": 0.3, "type": "pit_in", "car": 0}])
    pub.finish(None, [{"pos": 1, "entry_id": "a#1", "driver": "A", "status": "finished", "points": 25}])
    live = c.get("/api/live").json()
    assert live["live"] and live["round_id"] == 5 and live["session"] == "race"
    t0 = time.time()
    with c.websocket_connect("/ws/live") as ws:
        msgs = []
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] == "end":
                break
    kinds = [m["type"] for m in msgs]
    assert kinds[0] == "init" and kinds[-1] == "end" and "event" in kinds
    assert msgs[0]["replay"]["cars"][0]["entry_id"] == "a#1"
    assert [m["frame"][0] for m in msgs if m["type"] == "frame"][-1] == 0.8
    assert time.time() - t0 < 5.0
    state = json.loads((tmp_path / "live" / STATE_FILE).read_text())
    assert state["finished"]


def test_replay_gzip_file_is_strict_json(api):
    path = next((api.dir / "replays").glob("*-race.json.gz"))
    text = gzip.decompress(path.read_bytes()).decode()
    assert "NaN" not in text and "Infinity" not in text
    json.loads(text)
