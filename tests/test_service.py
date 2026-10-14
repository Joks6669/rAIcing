"""LeagueService: lifecycle end to end on SQLite (1-lap races, few cars)."""
from __future__ import annotations

import json
import time
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from conftest import give_brain, setup_league
from raicing.brains.spec import mlp_brain
from raicing.league import economy
from raicing.league import rating as rating_mod
from raicing.league.live import STATE_FILE
from raicing.league.models import (EntryRecord, GarageTx, QuotaLedger, RegulationChangeRecord, ResultRow, Round,
                                   Season, TeamPenalty, utcnow)
from raicing.league.service import Conflict, Forbidden, LeagueError, NotFound, Unauthorized
from raicing.league.util import derived_seed, sha256_hex


# ---------------------------------------------------------------------- league & teams
def test_create_league_hides_secret_and_seeds(make_service):
    svc = make_service()
    setup_league(svc, n_rounds=3)
    league = svc._league()
    rounds = svc.s.scalars(select(Round).order_by(Round.number)).all()
    assert [r.number for r in rounds] == [1, 2, 3]
    assert rounds[0].track_seed == derived_seed(league.secret, 1, 1, "track")
    assert rounds[0].weather_seed == derived_seed(league.secret, 1, 1, "weather")
    assert all(r.category in ("rapide", "mixte", "technique") and 10 <= r.laps <= 15 for r in rounds)
    public = json.dumps([svc.get_league(), svc.get_season(1), svc.get_round(rounds[0].id), svc.list_teams()])
    assert league.secret not in public
    assert str(rounds[0].track_seed) not in public and rounds[0].track_name not in public
    rd = svc.get_round(rounds[0].id)
    assert rd["track"]["name"] is None and rd["track"]["seed"] is None and rd["quali"] is None
    assert svc.get_season(1)["status"] == "planned" and svc.get_season(1)["budget"] == 1000
    with pytest.raises(Conflict):
        svc.create_league("Bis")


def test_teams_tokens_and_system_teams(make_service):
    svc = make_service()
    tokens = setup_league(svc, total_teams=4)
    tok = tokens["astra-racing"]
    assert tok.startswith("rai_") and len(tok) > 30
    assert svc.authenticate(tok) == "astra-racing"
    with pytest.raises(Unauthorized):
        svc.authenticate("rai_wrong")
    team = svc._team("astra-racing")
    assert team.token_hash == sha256_hex(tok)
    dumped = json.dumps(svc.get_team("astra-racing"))
    assert tok not in dumped and team.token_hash not in dumped
    teams = svc.list_teams()
    assert len(teams) == 4
    system = [t for t in teams if t["is_system"]]
    assert len(system) == 2 and all(t["reference"] for t in system)
    numbers = [d["number"] for t in teams for d in t["drivers"]]
    assert len(numbers) == 8 and len(set(numbers)) == 8
    assert [d["id"] for d in svc.get_team("astra-racing")["drivers"]] == ["astra-racing-1", "astra-racing-2"]
    with pytest.raises(Conflict):
        svc.create_team("Astra Racing", "#123456")
    with pytest.raises(LeagueError):
        svc.create_team("Couleur", "rouge")
    rotated = svc.rotate_token("astra-racing")["token"]
    with pytest.raises(Unauthorized):
        svc.authenticate(tok)
    assert svc.authenticate(rotated) == "astra-racing"


# ---------------------------------------------------------------------- garage
def test_garage_budget_capacity_and_errors(make_service):
    svc = make_service()
    setup_league(svc)
    svc.start_season(1)
    out = svc.upgrade_part("astra-racing", "aero")
    assert out["cost"] == 65.0 and out["garage"]["available"] == 935.0 and out["garage"]["parts"]["aero"] == 1
    # capacity: engine thermal load 2/level against a capacity of 8
    for _ in range(4):
        svc.upgrade_part("astra-racing", "engine")
    with pytest.raises(LeagueError, match="charge thermique"):
        svc.upgrade_part("astra-racing", "engine")
    with pytest.raises(LeagueError, match="pièce inconnue"):
        svc.upgrade_part("astra-racing", "turbo")
    assert svc.research_technology("astra-racing", "tyre_telemetry")["cost"] == 80
    with pytest.raises(LeagueError, match="déjà acquise"):
        svc.research_technology("astra-racing", "tyre_telemetry")
    with pytest.raises(LeagueError, match="conforme"):
        svc.revise_part("astra-racing", "aero")
    # budget: the chassis has no thermal load, buy it until the money runs out
    with pytest.raises(LeagueError, match="budget insuffisant"):
        for _ in range(10):
            svc.upgrade_part("astra-racing", "chassis")
    g = svc.get_catalog("astra-racing")
    chassis = g["parts"][3]
    assert chassis["part"] == "chassis" and chassis["level"] == 3 and chassis["next_cost"] > g["available"]
    assert g["available"] >= 0 and g["spent"] + g["available"] == pytest.approx(g["budget"])
    n_tx = svc.s.scalar(select(func.count(GarageTx.id)).where(GarageTx.team_id == "astra-racing"))
    assert n_tx == 1 + 4 + 1 + 3
    with pytest.raises(Forbidden):
        svc.upgrade_part("boreal-racing", "aero")


# ---------------------------------------------------------------------- entries
def test_entry_validation(make_service):
    svc = make_service()
    setup_league(svc, n_rounds=2)
    a1, a2 = give_brain(svc, "astra-racing", 1), give_brain(svc, "astra-racing", 2)
    b1 = give_brain(svc, "opus-racing", 1)
    r1, r2 = [r["id"] for r in svc.get_season(1)["rounds"]]
    with pytest.raises(Conflict, match="pas en cours"):
        svc.submit_entry("astra-racing", r1, "astra-racing-1", a1)
    svc.start_season(1)
    with pytest.raises(Conflict, match="prochaine manche"):
        svc.submit_entry("astra-racing", r2, "astra-racing-1", a1)
    with pytest.raises(Forbidden):
        svc.submit_entry("astra-racing", r1, "astra-racing-1", b1)
    with pytest.raises(Forbidden):
        svc.submit_entry("astra-racing", r1, "opus-racing-1", a1)
    with pytest.raises(Conflict, match="distincts"):
        svc.submit_entry("astra-racing", r1, "astra-racing-1", a2)
    with pytest.raises(LeagueError, match="réglage inconnu"):
        svc.submit_entry("astra-racing", r1, "astra-racing-1", a1, {"turbo": 1})
    with pytest.raises(LeagueError, match="gomme inconnue"):
        svc.submit_entry("astra-racing", r1, "astra-racing-1", a1, {"start_compound": "slick"})
    with pytest.raises(LeagueError, match="pas de cerveau actif"):
        svc.submit_entry("opus-racing", r1, "opus-racing-2")
    out = svc.submit_entry("astra-racing", r1, "astra-racing-1", a1, {"wing": 2.0, "brake_bias": 0.9,
                                                                     "start_compound": "hard"})
    assert out["setup"] == {"wing": 1.0, "brake_bias": 0.66, "start_compound": "hard", "quali_compound": "soft"}
    assert out["entry_id"] == "astra-racing#1"
    # the explicit entry is visible (without its setup) before the race
    pre = svc.get_round(r1)
    assert [e["driver_id"] for e in pre["entries"]] == ["astra-racing-1"] and "setup" not in pre["entries"][0]
    with pytest.raises(Conflict, match="distincts"):
        svc.activate_brain("astra-racing", "astra-racing-2", a1)
    with pytest.raises(Conflict, match="doit être courue avant"):
        svc.run_round(r2)


# ---------------------------------------------------------------------- full season
def test_season_lifecycle_end_to_end(make_service, tmp_path):
    svc = make_service()
    setup_league(svc, n_rounds=1, total_teams=3)
    a1, _a2 = give_brain(svc, "astra-racing", 1), give_brain(svc, "astra-racing", 2)
    give_brain(svc, "opus-racing", 1)          # opus-racing-2 has no brain: forfeit
    svc.start_season(1)
    for part in economy.PARTS:                  # every part >= 1 so any regulation change hits the garage
        svc.upgrade_part("astra-racing", part)
    rid = svc.get_season(1)["rounds"][0]["id"]
    svc.submit_entry("astra-racing", rid, "astra-racing-1", a1, {"wing": 0.7, "start_compound": "medium"})

    # the worker path: schedule in the past, tick runs the round
    svc.schedule_rounds(1, utcnow() - timedelta(minutes=5), timedelta(hours=1))
    t0 = time.time()
    assert svc.tick() == [rid]
    assert time.time() - t0 < 60
    rd = svc.get_round(rid)
    assert rd["status"] == "done" and rd["track"]["name"] and rd["track"]["seed"] and rd["laps"] == 1
    assert rd["has_replay"] == {"quali": True, "race": True}
    n_cars = 2 + 1 + 2   # astra x2, opus x1, one system team x2
    assert len(rd["quali"]["classification"]) == n_cars and len(rd["race"]["classification"]) == n_cars
    assert rd["forfeits"] == [{"driver_id": "opus-racing-2", "team_id": "opus-racing", "reason": "aucun cerveau actif"}]
    entries = {e["driver_id"]: e for e in rd["entries"]}
    assert entries["astra-racing-1"]["submitted"] and entries["astra-racing-1"]["setup"]["wing"] == 0.7
    assert not entries["astra-racing-2"]["submitted"] and entries["astra-racing-2"]["brain"]["generation"] == 1
    assert entries["boreal-racing-1"]["brain"] is None and entries["boreal-racing-1"]["reference"]
    rows = svc.s.scalars(select(ResultRow).where(ResultRow.round_id == rid)).all()
    assert len(rows) == n_cars and sorted(r.grid for r in rows) == list(range(1, n_cars + 1))
    assert sum(r.points for r in rows) == sum(c["points"] for c in rd["race"]["classification"])
    assert (tmp_path / "replays" / f"{rid}-race.json.gz").exists()
    assert svc.get_season(1)["rounds"][0]["winner"] is not None

    # standings: drivers by points, teams = drivers - penalties
    st = svc.get_standings(1)
    pts = [d["points"] for d in st["drivers"]]
    assert pts == sorted(pts, reverse=True) and all(len(d["by_round"]) == 1 for d in st["drivers"])
    astra_before = next(t for t in st["teams"] if t["team_id"] == "astra-racing")["points"]
    drivers_sum = sum(d["points"] for d in st["drivers"] if d["team_id"] == "astra-racing")
    svc.s.add(TeamPenalty(round_id=rid, team_id="astra-racing", points=15, reason="test"))
    svc.s.commit()
    st = svc.get_standings(1)
    astra = next(t for t in st["teams"] if t["team_id"] == "astra-racing")
    assert astra["points"] == drivers_sum - 15 + (astra_before - drivers_sum) and astra["penalties"] >= 15
    assert astra["by_round"] == [astra["points"]]
    assert svc.get_team("astra-racing")["results"][0]["points"] == astra["points"]

    # live broadcast: finished at 1e6x speed -> not live; a running broadcast embargoes the results
    assert svc.get_live() == {"live": False}
    state = json.loads((tmp_path / "live" / STATE_FILE).read_text())
    assert state["round_id"] == rid and state["finished"]
    state.update(started_at=time.time(), speed=1.0, last_t=500.0)
    (tmp_path / "live" / STATE_FILE).write_text(json.dumps(state))
    assert svc.get_live()["live"] is True and svc.get_live()["round_id"] == rid
    hidden = svc.get_round(rid)
    assert hidden["status"] == "running" and hidden["race"] is None and hidden["track"]["name"] is None
    assert all(d["by_round"] == [None] for d in svc.get_standings(1)["drivers"])
    with pytest.raises(NotFound):
        svc.get_replay_path(rid)
    assert svc.get_round(rid, embargo=False)["race"] is not None
    (tmp_path / "live" / STATE_FILE).unlink()

    # end of season: regulation change, season 2 planned, garages roll over
    a_garage_s1 = economy.GarageState.from_dict(svc._ensure_garage("astra-racing", svc._season(1)).state)
    out = svc.close_season(1)
    assert out["next_season"] == 2 and out["change"]["parameter"]
    with pytest.raises(Conflict):
        svc.close_season(1)
    s2 = svc.s.get(Season, 2)
    assert s2.status == "planned" and s2.change["path"] == out["change"]["parameter"]
    ch = svc.s.scalars(select(RegulationChangeRecord)).one()
    assert ch.season == 2 and 5.0 <= abs(ch.percent) <= 15.0
    regs2 = svc.get_regulations(2)
    assert regs2["version"] == "S2" and len(regs2["changes"]) == 1
    node = regs2["regulations"]
    for part in ch.path.split("."):
        node = node[part]
    assert node == pytest.approx(ch.new)
    bench = svc._league().settings["bench"]
    assert [b["pos"] for b in bench] == [1, 2]
    svc.start_season(2)
    g2 = svc.get_team("astra-racing")["garage"]
    budget2 = economy.season_budget(2, svc._league_seed())
    assert g2["season"] == 2 and s2.budget == budget2
    assert g2["budget"] == pytest.approx(budget2 + min(a_garage_s1.available, 0.2 * budget2))
    assert g2["parts"] == a_garage_s1.parts and g2["spent"] == 0
    if ch.affects:
        assert g2["non_compliant"] == {ch.affects: int(a_garage_s1.parts[ch.affects] * 0.6)}
        assert svc.revise_part("astra-racing", ch.affects)["cost"] > 0
    assert len(svc.get_season(2)["rounds"]) == 1


def test_run_round_requires_active_season(make_service):
    svc = make_service()
    setup_league(svc, n_rounds=1)
    rid = svc.get_season(1)["rounds"][0]["id"]
    with pytest.raises(Conflict):
        svc.run_round(rid)
    with pytest.raises(Conflict):
        svc.run_next()
    with pytest.raises(Conflict):
        svc.open_entries(rid)


# ---------------------------------------------------------------------- lab jobs & quota
def test_jobs_quota_and_activation(make_service, fake_lab, monkeypatch):
    svc = make_service()
    setup_league(svc)
    svc.start_season(1)
    b_brain = give_brain(svc, "opus-racing", 1)
    with pytest.raises(LeagueError, match="driver_id"):
        svc.create_job("astra-racing", {"method": "es", "steps": 100})
    with pytest.raises(Forbidden):
        svc.create_job("astra-racing", {"driver_id": "opus-racing-1", "method": "es", "steps": 100})
    with pytest.raises(Forbidden):
        svc.create_job("astra-racing", {"driver_id": "astra-racing-1", "method": "es", "steps": 100,
                                        "init_from": b_brain})
    with pytest.raises(NotFound):
        svc.create_job("astra-racing", {"driver_id": "astra-racing-1", "method": "es", "steps": 100,
                                        "curriculum": {"mode": "traffic", "opponents": ["pro", "999"]}})
    j1 = svc.create_job("astra-racing", {"driver_id": "astra-racing-1", "method": "es", "steps": 5000,
                                         "activate": True})
    j2 = svc.create_job("astra-racing", {"driver_id": "astra-racing-2", "method": "bc", "steps": 100})
    assert j1["status"] == "queued" and j1["config"]["activate"] is True
    with pytest.raises(Conflict, match="simultanés"):
        svc.create_job("astra-racing", {"driver_id": "astra-racing-2", "method": "es", "steps": 100})
    assert svc.cancel_job("astra-racing", j2["id"])["status"] == "cancelled"
    with pytest.raises(Conflict):
        svc.cancel_job("astra-racing", j2["id"])
    with pytest.raises(Forbidden):
        svc.get_job("opus-racing", j1["id"])
    assert svc.next_queued_job() == j1["id"]
    done = svc.run_job_now(j1["id"])
    assert done["status"] == "done" and done["steps_used"] == 5000 and done["result_brain_id"]
    assert svc.get_team("astra-racing")["drivers"][0]["active_brain"]["id"] == done["result_brain_id"]
    brain = svc.get_brain("astra-racing", done["result_brain_id"])
    assert brain["generation"] == 1 and brain["training_steps"] == 5000 and brain["meta"]["job_id"] == j1["id"]
    with pytest.raises(Conflict):
        svc.run_job_now(j1["id"])
    assert svc.get_me("astra-racing")["quota"]["used"] == 5000
    # continued training from the new generation, quota nearly exhausted
    season = svc.s.get(Season, 1)
    season.quota = 5300
    svc.s.commit()
    j3 = svc.create_job("astra-racing", {"driver_id": "astra-racing-1", "method": "es", "steps": 1000,
                                         "init_from": done["result_brain_id"]})
    out3 = svc.run_job_now(j3["id"])
    assert out3["steps_used"] == 300
    child = svc.get_brain("astra-racing", out3["result_brain_id"])
    assert child["generation"] == 2 and child["parent"] == done["result_brain_id"] and not child["active_for"]
    with pytest.raises(Conflict, match="quota"):
        svc.create_job("astra-racing", {"driver_id": "astra-racing-1", "method": "es", "steps": 10})
    used = svc.s.scalar(select(func.sum(QuotaLedger.steps)).where(QuotaLedger.team_id == "astra-racing"))
    assert used == 5300


def test_failed_job_charges_reported_steps(make_service, fake_lab, monkeypatch):
    svc = make_service()
    setup_league(svc)
    svc.start_season(1)

    def boom(config, regs, init_brain, quota_remaining, workdir, progress=None, should_stop=None, **_):
        progress({"steps": 1234})
        raise RuntimeError("divergence")
    monkeypatch.setattr(fake_lab, "run_job", boom)
    job = svc.create_job("astra-racing", {"driver_id": "astra-racing-1", "method": "es", "steps": 5000})
    out = svc.run_job_now(job["id"])
    assert out["status"] == "failed" and "divergence" in out["error"] and out["steps_used"] == 1234
    assert svc.quota_used("astra-racing", 1) == 1234


def test_rating_and_import_policy(make_service, monkeypatch):
    svc = make_service()
    setup_league(svc)
    svc.start_season(1)
    bid = give_brain(svc, "astra-racing", 1)
    fake = rating_mod.Rating(61.5, (57.0, 66.0), {k: 60.0 for k in rating_mod.WEIGHTS})
    monkeypatch.setattr(rating_mod, "rate", lambda pilot, regs=None, cfg=None: fake)
    r = svc.rate_brain(bid, quick=True)
    assert r["overall"] == 61.5 and r["ci"] == [57.0, 66.0]
    drv = svc.get_driver("astra-racing-1")
    assert drv["rating"]["overall"] == 61.5 and drv["generations"][0]["rating"] == 61.5
    job = svc.request_rating("astra-racing", bid)
    assert job["kind"] == "rating" and svc.request_rating("astra-racing", bid)["id"] == job["id"]
    done = svc.run_job_now(job["id"])
    assert done["status"] == "done" and done["progress"]["rating"]["overall"] == 61.5
    assert svc.quota_used("astra-racing", 1) == 0
    with pytest.raises(Forbidden):
        svc.request_rating("opus-racing", bid)
    # brain import: forbidden by the default regulations, allowed when the rules say so
    with pytest.raises(Forbidden, match="cerveaux externes"):
        svc.import_brain("astra-racing", "astra-racing-2", mlp_brain([8]))
    season = svc.s.get(Season, 1)
    regs = json.loads(json.dumps(season.regulations))
    regs["training"]["allow_external_brains"] = True
    season.regulations = regs
    svc.s.commit()
    imp = svc.import_brain("astra-racing", "astra-racing-2", mlp_brain([8]))
    assert imp["imported"] and imp["method"] == "external" and imp["generation"] == 1
    with pytest.raises(LeagueError, match="cerveau invalide"):
        bad = mlp_brain([8])
        bad.spec["obs_version"] = 99
        svc.import_brain("astra-racing", "astra-racing-2", bad)


def test_entries_table_unique(make_service):
    svc = make_service()
    setup_league(svc)
    a1 = give_brain(svc, "astra-racing", 1)
    svc.start_season(1)
    rid = svc.get_season(1)["rounds"][0]["id"]
    svc.submit_entry("astra-racing", rid, "astra-racing-1", a1, {"wing": 0.2})
    svc.submit_entry("astra-racing", rid, "astra-racing-1", a1, {"wing": 0.3})
    rows = svc.s.scalars(select(EntryRecord).where(EntryRecord.round_id == rid)).all()
    assert len(rows) == 1 and rows[0].setup["wing"] == 0.3
