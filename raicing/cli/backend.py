"""CLI backends: ``LocalBackend`` calls ``LeagueService`` on ``RAICING_LEAGUE_DIR``; ``RemoteBackend``
calls the HTTP API (``RAICING_API_URL`` + ``RAICING_TOKEN`` / ``RAICING_ADMIN_TOKEN``).

Both expose the same methods and return the same JSON shapes (docs/API.md), so the commands in
``paddock``/``stewards`` are written once.
"""
from __future__ import annotations

import base64
import os
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator

from ..brains.spec import Brain
from .common import CliError

LONG_TIMEOUT = 1800.0   # races and season closings can take minutes on the server


class Backend:
    """Interface (documentation only)."""

    mode = "?"


# ====================================================================== local
class LocalBackend(Backend):
    mode = "local"

    def __init__(self, league_dir: str | Path, token: str | None = None, team: str | None = None,
                 create: bool = False):
        from ..league.db import Database, default_database_url
        from ..league.service import ServiceOptions

        self.dir = Path(league_dir).expanduser()
        if not create and not (self.dir / "league.db").exists() and not os.environ.get("DATABASE_URL"):
            raise CliError(f"aucune ligue locale dans {self.dir} (RAICING_LEAGUE_DIR) : "
                           "créez-la avec `stewards init`")
        self.dir.mkdir(parents=True, exist_ok=True)
        url = os.environ.get("DATABASE_URL") or default_database_url(self.dir)
        self.db = Database(url, self.dir)
        self.options = ServiceOptions.from_env()
        if "RAICING_LIVE" not in os.environ:
            self.options.live = False      # no broadcast in local mode unless asked
        self._token = token
        self._team = team
        self._team_id: str | None = None

    @contextmanager
    def svc(self) -> Iterator[Any]:
        from ..league.service import LeagueService
        with self.db.session() as s:
            yield LeagueService(s, self.dir, self.options)

    def team_id(self) -> str:
        if self._team_id is None:
            with self.svc() as svc:
                if self._token:
                    self._team_id = svc.authenticate(self._token)
                elif self._team:
                    t = svc.get_team(self._team)
                    if t["is_system"]:
                        raise CliError("une écurie système ne se pilote pas avec paddock")
                    self._team_id = t["id"]
                else:
                    raise CliError("écurie inconnue : définissez RAICING_TOKEN (ou RAICING_TEAM en mode local)")
        return self._team_id

    def _call(self, name: str, *args: Any, team: bool = False, **kwargs: Any) -> Any:
        tid = self.team_id() if team else None
        with self.svc() as svc:
            fn = getattr(svc, name)
            return fn(tid, *args, **kwargs) if team else fn(*args, **kwargs)

    # ---- public reads
    def league(self) -> dict: return self._call("get_league")
    def season(self, n: int) -> dict: return self._call("get_season", n)
    def standings(self, n: int) -> dict: return self._call("get_standings", n)
    def round(self, rid: int) -> dict: return self._call("get_round", rid)
    def regulations(self, n: int | None) -> dict: return self._call("get_regulations", n)
    def teams(self) -> list: return self._call("list_teams")
    def team(self, tid: str) -> dict: return self._call("get_team", tid)
    def driver(self, did: str) -> dict: return self._call("get_driver", did)
    def live(self) -> dict: return self._call("get_live")

    def replay(self, rid: int, session: str) -> bytes:
        import gzip
        with self.svc() as svc:
            return gzip.decompress(svc.get_replay_path(rid, session).read_bytes())

    # ---- team space
    def me(self) -> dict: return self._call("get_me", team=True)
    def catalog(self) -> dict: return self._call("get_catalog", team=True)
    def upgrade(self, part: str) -> dict: return self._call("upgrade_part", part, team=True)
    def research(self, tech: str) -> dict: return self._call("research_technology", tech, team=True)
    def revise(self, part: str) -> dict: return self._call("revise_part", part, team=True)
    def brains(self) -> list: return self._call("list_brains", team=True)
    def brain(self, bid: int) -> dict: return self._call("get_brain", bid, team=True)
    def export_brain(self, bid: int) -> bytes: return self._call("export_brain", bid, team=True)
    def activate(self, driver: str, bid: int) -> dict: return self._call("activate_brain", driver, bid, team=True)

    def import_brain(self, driver: str, brain: Brain, method: str) -> dict:
        return self._call("import_brain", driver, brain, method=method, team=True)

    def rate(self, bid: int, quick: bool | None, wait: bool,
             on_progress: Callable[[dict], None] | None = None) -> dict:
        tid = self.team_id()
        with self.svc() as svc:
            svc.get_brain(tid, bid)          # ownership check
            rating = svc.rate_brain(bid, quick=quick)
        return {"status": "done", "brain_id": bid, "rating": rating}

    def create_job(self, config: dict) -> dict: return self._call("create_job", config, team=True)
    def jobs(self) -> list: return self._call("list_jobs", team=True)
    def job(self, jid: int) -> dict: return self._call("get_job", jid, team=True)
    def cancel(self, jid: int) -> dict: return self._call("cancel_job", jid, team=True)

    def train(self, config: dict, wait: bool, on_progress: Callable[[dict], None] | None = None) -> dict:
        """Local mode: the job is created then executed in this process (the quota is recorded)."""
        job = self.create_job(config)
        with self.svc() as svc:
            return svc.run_job_now(job["id"])

    def submit_entry(self, rid: int, driver: str, bid: int | None, setup: dict) -> dict:
        return self._call("submit_entry", rid, driver, bid, setup, team=True)

    # ---- commissioners
    def init(self, name: str, seed: int | None, rounds: int, system_teams: int) -> dict:
        with self.svc() as svc:
            svc.create_league(name, seed=seed, n_rounds=rounds)
            created = svc.ensure_system_teams(system_teams) if system_teams > 0 else []
            return {"league": svc.get_league(), "system_teams": created, "league_dir": str(self.dir)}

    def create_team(self, name: str, color: str, director: str, drivers: list[str] | None) -> dict:
        return self._call("create_team", name, color, director, driver_names=drivers)

    def rotate_token(self, tid: str) -> dict: return self._call("rotate_token", tid)
    def fill(self, total: int) -> dict: return {"created": self._call("ensure_system_teams", total)}
    def season_start(self, n: int) -> dict: return self._call("start_season", n)
    def season_close(self, n: int) -> dict: return self._call("close_season", n)

    def season_schedule(self, n: int, start: str, every_hours: float) -> dict:
        from ..league.util import parse_iso
        return self._call("schedule_rounds", n, parse_iso(start), timedelta(hours=every_hours))

    def round_open(self, rid: int) -> dict: return self._call("open_entries", rid)

    def round_run(self, rid: int, background: bool) -> dict:
        return self._call("request_run" if background else "run_round", rid)

    def run_next(self) -> dict: return self._call("run_next")


# ====================================================================== remote
class RemoteBackend(Backend):
    mode = "remote"

    def __init__(self, api_url: str, token: str | None):
        if not api_url:
            raise CliError("RAICING_API_URL n'est pas défini")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = self._make_client(api_url.rstrip("/"), headers)
        self._has_token = bool(token)

    @staticmethod
    def _make_client(base_url: str, headers: dict[str, str]) -> Any:
        """HTTP client (tests substitute a FastAPI ``TestClient``)."""
        import httpx
        return httpx.Client(base_url=base_url, headers=headers, timeout=60.0)

    def _req(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
             timeout: float | None = None, raw: bool = False) -> Any:
        kwargs: dict[str, Any] = {"json": json, "params": params}
        if timeout is not None:
            kwargs["timeout"] = timeout
        r = self.client.request(method, path, **kwargs)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            if isinstance(detail, list):   # pydantic validation errors
                detail = "; ".join(f"{'.'.join(map(str, d.get('loc', [])))} : {d.get('msg')}" for d in detail)
            raise CliError(str(detail), r.status_code)
        return r.content if raw else r.json()

    def _need_token(self) -> None:
        if not self._has_token:
            raise CliError("RAICING_TOKEN n'est pas défini")

    # ---- public reads
    def league(self) -> dict: return self._req("GET", "/api/league")
    def season(self, n: int) -> dict: return self._req("GET", f"/api/seasons/{n}")
    def standings(self, n: int) -> dict: return self._req("GET", f"/api/seasons/{n}/standings")
    def round(self, rid: int) -> dict: return self._req("GET", f"/api/rounds/{rid}")

    def regulations(self, n: int | None) -> dict:
        return self._req("GET", "/api/regulations" if n is None else f"/api/regulations/{n}")

    def teams(self) -> list: return self._req("GET", "/api/teams")
    def team(self, tid: str) -> dict: return self._req("GET", f"/api/teams/{tid}")
    def driver(self, did: str) -> dict: return self._req("GET", f"/api/drivers/{did}")
    def live(self) -> dict: return self._req("GET", "/api/live")

    def replay(self, rid: int, session: str) -> bytes:
        return self._req("GET", f"/api/rounds/{rid}/replay", params={"session": session}, raw=True,
                         timeout=300.0)

    # ---- team space
    def me(self) -> dict:
        self._need_token()
        return self._req("GET", "/api/me")

    def catalog(self) -> dict: return self._req("GET", "/api/garage/catalog")
    def upgrade(self, part: str) -> dict: return self._req("POST", "/api/garage/upgrade", json={"part": part})
    def research(self, tech: str) -> dict: return self._req("POST", "/api/garage/research", json={"technology": tech})
    def revise(self, part: str) -> dict: return self._req("POST", "/api/garage/revise", json={"part": part})
    def brains(self) -> list: return self._req("GET", "/api/brains")
    def brain(self, bid: int) -> dict: return self._req("GET", f"/api/brains/{bid}")
    def export_brain(self, bid: int) -> bytes: return self._req("GET", f"/api/brains/{bid}/package", raw=True)

    def activate(self, driver: str, bid: int) -> dict:
        return self._req("POST", f"/api/drivers/{driver}/activate", json={"brain_id": bid})

    def import_brain(self, driver: str, brain: Brain, method: str) -> dict:
        package = base64.b64encode(brain.to_bytes()).decode("ascii")
        return self._req("POST", "/api/brains/import", json={"driver_id": driver, "package": package,
                                                             "method": method})

    def _wait_job(self, jid: int, on_progress: Callable[[dict], None] | None, poll: float = 10.0) -> dict:
        while True:
            job = self.job(jid)
            if job["status"] not in ("queued", "running"):
                return job
            if on_progress is not None:
                on_progress(job)
            time.sleep(poll)

    def rate(self, bid: int, quick: bool | None, wait: bool,
             on_progress: Callable[[dict], None] | None = None) -> dict:
        job = self._req("POST", f"/api/brains/{bid}/rating", json={"quick": quick})
        if not wait:
            return job
        job = self._wait_job(job["id"], on_progress)
        if job["status"] != "done":
            raise CliError(f"Driver Rating en échec : {job.get('error')}")
        return {"status": "done", "brain_id": bid, "rating": job["progress"].get("rating")}

    def create_job(self, config: dict) -> dict: return self._req("POST", "/api/lab/jobs", json=config)
    def jobs(self) -> list: return self._req("GET", "/api/lab/jobs")
    def job(self, jid: int) -> dict: return self._req("GET", f"/api/lab/jobs/{jid}")
    def cancel(self, jid: int) -> dict: return self._req("DELETE", f"/api/lab/jobs/{jid}")

    def train(self, config: dict, wait: bool, on_progress: Callable[[dict], None] | None = None) -> dict:
        job = self.create_job(config)
        return self._wait_job(job["id"], on_progress) if wait else job

    def submit_entry(self, rid: int, driver: str, bid: int | None, setup: dict) -> dict:
        return self._req("POST", f"/api/rounds/{rid}/entries",
                         json={"driver_id": driver, "brain_id": bid, "setup": setup})

    # ---- commissioners
    def init(self, name: str, seed: int | None, rounds: int, system_teams: int) -> dict:
        return self._req("POST", "/api/admin/league", json={"name": name, "seed": seed, "rounds": rounds,
                                                           "system_teams": system_teams}, timeout=LONG_TIMEOUT)

    def create_team(self, name: str, color: str, director: str, drivers: list[str] | None) -> dict:
        return self._req("POST", "/api/admin/teams", json={"name": name, "color": color, "director": director,
                                                          "drivers": drivers})

    def rotate_token(self, tid: str) -> dict: return self._req("POST", f"/api/admin/teams/{tid}/token")
    def fill(self, total: int) -> dict: return self._req("POST", "/api/admin/system-teams", json={"total": total})
    def season_start(self, n: int) -> dict: return self._req("POST", f"/api/admin/seasons/{n}/start")

    def season_close(self, n: int) -> dict:
        return self._req("POST", f"/api/admin/seasons/{n}/close", timeout=LONG_TIMEOUT)

    def season_schedule(self, n: int, start: str, every_hours: float) -> dict:
        return self._req("POST", f"/api/admin/seasons/{n}/schedule", json={"start": start, "every_hours": every_hours})

    def round_open(self, rid: int) -> dict: return self._req("POST", f"/api/admin/rounds/{rid}/open")

    def round_run(self, rid: int, background: bool) -> dict:
        return self._req("POST", f"/api/admin/rounds/{rid}/run", params={"background": str(background).lower()},
                         timeout=LONG_TIMEOUT)

    def run_next(self) -> dict: return self._req("POST", "/api/admin/run-next", timeout=LONG_TIMEOUT)


def make_backend(cfg: dict[str, Any], admin: bool = False, create: bool = False) -> LocalBackend | RemoteBackend:
    """Backend for ``paddock`` (team token) or ``stewards`` (``admin=True``: admin token in remote mode)."""
    if cfg.get("mode") == "remote":
        token = cfg.get("admin_token") if admin else cfg.get("token")
        return RemoteBackend(cfg.get("api_url", ""), token)
    league_dir = cfg.get("league_dir")
    if not league_dir:
        raise CliError("RAICING_LEAGUE_DIR n'est pas défini (mode local)")
    return LocalBackend(league_dir, token=cfg.get("token"), team=cfg.get("team"), create=create)
