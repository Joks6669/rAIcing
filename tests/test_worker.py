"""Worker loop: queued jobs and round opening."""
from __future__ import annotations

from conftest import FAST, setup_league
from raicing.api.settings import ApiSettings
from raicing.api.worker import Worker
from raicing.league.service import LeagueService, ServiceOptions


def test_worker_step_runs_queued_jobs(tmp_path, fake_lab):
    settings = ApiSettings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'w.db'}",
                           options=ServiceOptions(**FAST), worker_interval=0.01)
    worker = Worker(settings)
    with worker.db.session() as s:
        svc = LeagueService(s, tmp_path, settings.options)
        setup_league(svc)
        svc.start_season(1)
        job = svc.create_job("astra-racing", {"driver_id": "astra-racing-1", "method": "es", "steps": 800,
                                              "activate": True})
    assert worker.step() is True
    with worker.db.session() as s:
        svc = LeagueService(s, tmp_path, settings.options)
        done = svc.get_job("astra-racing", job["id"])
        assert done["status"] == "done" and done["steps_used"] == 800
        assert svc.get_me("astra-racing")["drivers"][0]["active_brain"]["id"] == done["result_brain_id"]
        # the next round's entries were opened by the heartbeat, but it has no date: nothing ran
        assert svc.get_season(1)["rounds"][0]["status"] == "entries_open"
    assert worker.step() is False
    worker.db.dispose()
