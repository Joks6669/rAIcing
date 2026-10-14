"""HTTP API of the league (contract: docs/API.md).

``create_app(settings)`` builds the FastAPI application: public read endpoints, team endpoints
(``Authorization: Bearer <team token>``), commissioner endpoints (``Bearer <RAICING_ADMIN_TOKEN>``),
replays, the live feed (``GET /api/live``, ``WS /ws/live``) and ``GET /healthz``.
All business rules live in :class:`raicing.league.service.LeagueService`.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import gzip
import hmac
import time
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from ..brains.spec import Brain
from ..league.db import Database
from ..league.live import STALE_AFTER, FeedTail, broadcast_time, is_live, live_status, message_time, read_state
from ..league.service import LeagueError, LeagueService
from ..league.util import parse_iso
from ..matches import MatchError, MatchRunner, MatchStore
from .settings import ApiSettings

# ---------------------------------------------------------------------- request bodies


class PartBody(BaseModel):
    part: str


class TechnologyBody(BaseModel):
    technology: str


class ActivateBody(BaseModel):
    brain_id: int


class EntryBody(BaseModel):
    driver_id: str
    brain_id: int | None = None
    setup: dict[str, Any] | None = None


class ImportBody(BaseModel):
    driver_id: str
    package: str = Field(description="paquet cerveau (zip brain.json + weights.npz) encodé en base64")
    method: str = "external"


class RatingBody(BaseModel):
    quick: bool | None = None


class LeagueBody(BaseModel):
    name: str = "AI Racing League"
    seed: int | None = None
    rounds: int = 10
    system_teams: int = 6


class TeamBody(BaseModel):
    name: str
    color: str = "#888888"
    director: str = ""
    drivers: list[str] | None = None


class SystemTeamsBody(BaseModel):
    total: int = 8


class SeasonCloseBody(BaseModel):
    close: int | None = None


class ScheduleBody(BaseModel):
    start: str
    every_hours: float = 168.0


class MatchEntrantBody(BaseModel):
    name: str = ""
    color: str | None = None
    start: str = "auto"
    package: str | None = Field(default=None, description="modèle (zip brain.json + weights.npz) en base64")
    reference: str | None = Field(default=None, description="pilote de référence si pas de modèle")


class MatchBody(BaseModel):
    name: str = ""
    entrants: list[MatchEntrantBody]
    track_seed: int | str | None = None
    laps: int | None = None
    weather: str = "dry"
    fill: int = 0


# ---------------------------------------------------------------------- helpers


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or ApiSettings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_url, settings.data_dir)
    live_dir = settings.data_dir / "live"

    app = FastAPI(title="AI Racing League", version="1", description="API de la ligue — contrat v1 (docs/API.md)")
    app.state.settings = settings
    app.state.db = db
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"], allow_credentials=False)

    @app.exception_handler(LeagueError)
    async def _league_error(_request: Request, exc: LeagueError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.exception_handler(MatchError)
    async def _match_error(_request: Request, exc: MatchError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    matches = MatchStore(settings.data_dir)
    runner = MatchRunner(matches)
    app.state.matches = matches

    # ------------------------------------------------------------------ dependencies
    def service() -> Iterator[LeagueService]:
        with db.session() as s:
            yield LeagueService(s, settings.data_dir, settings.options)

    def team(authorization: str | None = Header(default=None), svc: LeagueService = Depends(service)) -> str:
        token = _bearer(authorization)
        if token is None:
            raise HTTPException(401, "jeton d'écurie manquant (Authorization: Bearer <jeton>)",
                                headers={"WWW-Authenticate": "Bearer"})
        return svc.authenticate(token)

    def admin(authorization: str | None = Header(default=None)) -> None:
        token = _bearer(authorization)
        if token is None:
            raise HTTPException(401, "jeton de commissaire manquant", headers={"WWW-Authenticate": "Bearer"})
        if not settings.admin_token:
            raise HTTPException(403, "administration désactivée : RAICING_ADMIN_TOKEN n'est pas défini")
        if not hmac.compare_digest(token.encode(), settings.admin_token.encode()):
            raise HTTPException(403, "jeton de commissaire invalide")

    # ------------------------------------------------------------------ health
    @app.get("/healthz")
    def healthz(svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return {"ok": True, "league": svc.has_league()}

    # ------------------------------------------------------------------ public reads
    @app.get("/api/league")
    def league(svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.get_league()

    @app.get("/api/seasons/{number}")
    def season(number: int, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.get_season(number)

    @app.get("/api/seasons/{number}/standings")
    def standings(number: int, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.get_standings(number)

    @app.get("/api/rounds/{round_id}")
    def round_detail(round_id: int, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.get_round(round_id)

    @app.get("/api/rounds/{round_id}/replay")
    def replay(round_id: int, request: Request, session: str = Query("race"),
               svc: LeagueService = Depends(service)) -> Response:
        path = svc.get_replay_path(round_id, session)
        headers = {"Cache-Control": "public, max-age=3600", "Vary": "Accept-Encoding"}
        if "gzip" in request.headers.get("accept-encoding", "").lower():
            return FileResponse(path, media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})
        return Response(gzip.decompress(path.read_bytes()), media_type="application/json", headers=headers)

    # ------------------------------------------------------------------ exhibition races
    @app.post("/api/matches", status_code=201)
    def match_create(body: MatchBody) -> dict[str, Any]:
        packages: list[bytes | None] = []
        for k, e in enumerate(body.entrants):
            if e.package:
                try:
                    packages.append(base64.b64decode(e.package, validate=True))
                except (binascii.Error, ValueError) as exc:
                    raise MatchError(f"{e.name or f'participant {k + 1}'} : fichier mal transmis ({exc})") from exc
            else:
                packages.append(None)
        meta = matches.create(body.model_dump(exclude={"entrants": {"__all__": {"package"}}}), packages)
        runner.submit(meta["id"])
        return meta

    @app.get("/api/matches")
    def match_list() -> list[dict[str, Any]]:
        return matches.list()

    @app.get("/api/matches/{match_id}")
    def match_detail(match_id: str) -> dict[str, Any]:
        return matches.read(match_id)

    @app.get("/api/matches/{match_id}/replay")
    def match_replay(match_id: str, request: Request, session: str = Query("race")) -> Response:
        path = matches.replay_path(match_id, session)
        if "gzip" in request.headers.get("accept-encoding", "").lower():
            return FileResponse(path, media_type="application/json", headers={"Content-Encoding": "gzip"})
        return Response(gzip.decompress(path.read_bytes()), media_type="application/json")

    @app.get("/api/teams")
    def teams(svc: LeagueService = Depends(service)) -> list[dict[str, Any]]:
        return svc.list_teams()

    @app.get("/api/teams/{team_id}")
    def team_detail(team_id: str, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.get_team(team_id)

    @app.get("/api/drivers/{driver_id}")
    def driver(driver_id: str, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.get_driver(driver_id)

    @app.get("/api/regulations")
    def regulations_current(svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.get_regulations(None)

    @app.get("/api/regulations/{number}")
    def regulations(number: int, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.get_regulations(number)

    @app.get("/api/live")
    def live() -> dict[str, Any]:
        return live_status(live_dir)

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        await ws.accept()
        try:
            await _stream_live(ws, live_dir, settings.live_poll)
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            return

    # ------------------------------------------------------------------ team space
    @app.get("/api/me")
    def me(team_id: str = Depends(team), svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.get_me(team_id)

    @app.get("/api/garage/catalog")
    def catalog(team_id: str = Depends(team), svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.get_catalog(team_id)

    @app.post("/api/garage/upgrade")
    def upgrade(body: PartBody, team_id: str = Depends(team), svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.upgrade_part(team_id, body.part)

    @app.post("/api/garage/research")
    def research(body: TechnologyBody, team_id: str = Depends(team),
                 svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.research_technology(team_id, body.technology)

    @app.post("/api/garage/revise")
    def revise(body: PartBody, team_id: str = Depends(team), svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.revise_part(team_id, body.part)

    @app.get("/api/brains")
    def brains(team_id: str = Depends(team), svc: LeagueService = Depends(service)) -> list[dict[str, Any]]:
        return svc.list_brains(team_id)

    @app.post("/api/brains/import", status_code=201)
    def brain_import(body: ImportBody, team_id: str = Depends(team),
                     svc: LeagueService = Depends(service)) -> dict[str, Any]:
        try:
            brain = Brain.from_bytes(base64.b64decode(body.package, validate=True))
        except (binascii.Error, ValueError, KeyError, OSError) as exc:
            raise HTTPException(400, f"paquet cerveau illisible : {exc}") from exc
        except Exception as exc:  # zipfile.BadZipFile and numpy load errors
            raise HTTPException(400, f"paquet cerveau illisible : {exc}") from exc
        return svc.import_brain(team_id, body.driver_id, brain, method=body.method)

    @app.get("/api/brains/{brain_id}")
    def brain_detail(brain_id: int, team_id: str = Depends(team),
                     svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.get_brain(team_id, brain_id)

    @app.get("/api/brains/{brain_id}/package")
    def brain_package(brain_id: int, team_id: str = Depends(team), svc: LeagueService = Depends(service)) -> Response:
        data = svc.export_brain(team_id, brain_id)
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="brain-{brain_id}.zip"'})

    @app.post("/api/brains/{brain_id}/rating", status_code=202)
    def brain_rating(brain_id: int, body: RatingBody | None = None, team_id: str = Depends(team),
                     svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.request_rating(team_id, brain_id, quick=body.quick if body else None)

    @app.post("/api/drivers/{driver_id}/activate")
    def activate(driver_id: str, body: ActivateBody, team_id: str = Depends(team),
                 svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.activate_brain(team_id, driver_id, body.brain_id)

    @app.post("/api/lab/jobs", status_code=201)
    def create_job(config: dict[str, Any] = Body(...), team_id: str = Depends(team),
                   svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.create_job(team_id, config)

    @app.get("/api/lab/jobs")
    def jobs(team_id: str = Depends(team), svc: LeagueService = Depends(service)) -> list[dict[str, Any]]:
        return svc.list_jobs(team_id)

    @app.get("/api/lab/jobs/{job_id}")
    def job(job_id: int, team_id: str = Depends(team), svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.get_job(team_id, job_id)

    @app.delete("/api/lab/jobs/{job_id}")
    def cancel_job(job_id: int, team_id: str = Depends(team), svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.cancel_job(team_id, job_id)

    @app.post("/api/rounds/{round_id}/entries", status_code=201)
    def submit_entry(round_id: int, body: EntryBody, team_id: str = Depends(team),
                     svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.submit_entry(team_id, round_id, body.driver_id, body.brain_id, body.setup)

    # ------------------------------------------------------------------ commissioners
    @app.post("/api/admin/league", status_code=201, dependencies=[Depends(admin)])
    def admin_league(body: LeagueBody, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        out = svc.create_league(body.name, seed=body.seed, n_rounds=body.rounds)
        created = svc.ensure_system_teams(body.system_teams) if body.system_teams > 0 else []
        return {"league": svc.get_league() if created else out, "system_teams": created}

    @app.post("/api/admin/teams", status_code=201, dependencies=[Depends(admin)])
    def admin_team(body: TeamBody, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.create_team(body.name, body.color, body.director, driver_names=body.drivers)

    @app.post("/api/admin/teams/{team_id}/token", dependencies=[Depends(admin)])
    def admin_token(team_id: str, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.rotate_token(team_id)

    @app.post("/api/admin/system-teams", dependencies=[Depends(admin)])
    def admin_system_teams(body: SystemTeamsBody, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return {"created": svc.ensure_system_teams(body.total)}

    @app.post("/api/admin/seasons", dependencies=[Depends(admin)])
    def admin_next_season(body: SeasonCloseBody | None = None, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        number = body.close if body and body.close is not None else None
        if number is None:
            cur = svc.get_league()["current_season"]
            if cur is None:
                raise HTTPException(409, "aucune saison")
            number = cur
        return svc.close_season(number)

    @app.post("/api/admin/seasons/{number}/start", dependencies=[Depends(admin)])
    def admin_start(number: int, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.start_season(number)

    @app.post("/api/admin/seasons/{number}/close", dependencies=[Depends(admin)])
    def admin_close(number: int, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.close_season(number)

    @app.post("/api/admin/seasons/{number}/schedule", dependencies=[Depends(admin)])
    def admin_schedule(number: int, body: ScheduleBody, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        try:
            start = parse_iso(body.start)
        except ValueError as exc:
            raise HTTPException(400, f"date invalide : {body.start}") from exc
        return svc.schedule_rounds(number, start, timedelta(hours=body.every_hours))

    @app.post("/api/admin/rounds/{round_id}/open", dependencies=[Depends(admin)])
    def admin_open(round_id: int, svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.open_entries(round_id)

    @app.post("/api/admin/rounds/{round_id}/run", dependencies=[Depends(admin)])
    def admin_run(round_id: int, background: bool = Query(False),
                  svc: LeagueService = Depends(service)) -> dict[str, Any]:
        if background:
            return svc.request_run(round_id)
        return svc.run_round(round_id)

    @app.post("/api/admin/run-next", dependencies=[Depends(admin)])
    def admin_run_next(svc: LeagueService = Depends(service)) -> dict[str, Any]:
        return svc.run_next()

    return app


# ---------------------------------------------------------------------- live relay


async def _stream_live(ws: WebSocket, live_dir, poll: float) -> None:
    """Relay the current live feed at broadcast pace; late joiners get the backlog of events
    and the latest frame, then real time. One race per connection: closes after ``end``."""
    state = read_state(live_dir)
    if not is_live(state):
        await ws.send_json({"type": "idle", "live": False})
        await ws.close()
        return
    assert state is not None
    speed = float(state.get("speed", 1.0) or 1.0)
    tail = FeedTail(live_dir / state["file"])
    pending: dict[str, Any] | None = None
    last_data = time.time()
    try:
        while True:
            msgs = tail.read()
            if not msgs:
                current = read_state(live_dir)
                if current is None or current.get("round_id") != state.get("round_id") \
                        or time.time() - last_data > STALE_AFTER:
                    await ws.send_json({"type": "end", "aborted": "flux interrompu"})
                    await ws.close()
                    return
                await asyncio.sleep(poll)
                continue
            last_data = time.time()
            for msg in msgs:
                kind = msg.get("type")
                if kind == "init":
                    await ws.send_json(msg)
                    continue
                t = message_time(msg) if kind != "end" else float(msg.get("t", 0.0))
                now_b = broadcast_time(state)
                if kind == "frame" and t is not None and t < now_b - 1.0:
                    pending = msg          # late joiner: skip stale frames, keep the most recent one
                    continue
                if pending is not None:
                    await ws.send_json(pending)
                    pending = None
                if t is not None and t > now_b:
                    await asyncio.sleep((t - now_b) / speed)
                await ws.send_json(msg)
                if kind == "end":
                    await ws.close()
                    return
    finally:
        tail.close()
