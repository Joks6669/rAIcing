"""League services: the single implementation of the league rules (docs/LEAGUE.md).

``LeagueService`` operates on a SQLAlchemy session and a data directory (brains, replays, live
feed, training work dirs). The FastAPI app, the worker and the local ``paddock``/``stewards``
CLIs all call it. Every ``get_*`` method returns plain JSON-ready dicts whose shapes follow
docs/API.md. Mutating methods commit their own transaction.

Errors are raised as :class:`LeagueError` (with an HTTP-like ``status``) and carry French,
user-facing messages.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import secrets
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..brains.spec import Brain, BrainError
from ..config import COMPOUNDS, Regulations
from ..drivers.reference import PERSONALITIES
from ..physics.car import Setup, build_spec
from ..race import Entry, estimate_lap_time, race_laps, run_qualifying, run_race
from ..track import generate_track
from ..weather import generate_weather
from ..world import STATUS_NAMES
from . import economy
from . import rating as rating_mod
from . import regulations as regulations_mod
from . import training
from .live import LivePublisher, embargoed_round, live_status
from .models import (BrainRecord, Driver, EntryRecord, Garage, GarageTx, Job, League, QuotaLedger, RaceSession,
                     RatingRecord, RegulationChangeRecord, ResultRow, Round, Season, Team, TeamPenalty, utcnow)
from .util import aware, derived_seed, iso, sha256_hex, slugify, to_jsonable

log = logging.getLogger("raicing.league")

# ---------------------------------------------------------------------- errors


class LeagueError(Exception):
    """Business-rule violation; ``status`` mirrors the HTTP status the API returns."""

    status = 400

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status

    def __str__(self) -> str:
        return self.message


class NotFound(LeagueError):
    status = 404


class Unauthorized(LeagueError):
    status = 401


class Forbidden(LeagueError):
    status = 403


class Conflict(LeagueError):
    status = 409


# ---------------------------------------------------------------------- options


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "oui", "on")


@dataclass
class ServiceOptions:
    """Operational knobs (not game rules). ``from_env`` reads the ``RAICING_*`` variables."""

    laps_override: int | None = None      # RAICING_TEST_LAPS: race length override (tests, rehearsals)
    quali_time_limit: float | None = None  # RAICING_QUALI_TIME_LIMIT (simulated seconds)
    run_bench: bool = True                 # RAICING_RUN_BENCH: historical bench for regulation changes
    bench_attempts: int = 6
    live: bool = True                      # RAICING_LIVE: publish the live feed during races
    live_speed: float = 1.0                # RAICING_LIVE_SPEED: broadcast pace (1 = real time)
    embargo: bool = True                   # hide a round's results while its broadcast is running
    rating_quick: bool = False             # RAICING_RATING_QUICK: shorter Driver Rating battery

    @classmethod
    def from_env(cls) -> "ServiceOptions":
        laps = os.environ.get("RAICING_TEST_LAPS")
        ql = os.environ.get("RAICING_QUALI_TIME_LIMIT")
        return cls(laps_override=int(laps) if laps else None,
                   quali_time_limit=float(ql) if ql else None,
                   run_bench=_env_bool("RAICING_RUN_BENCH", True),
                   live=_env_bool("RAICING_LIVE", True),
                   live_speed=float(os.environ.get("RAICING_LIVE_SPEED", "1.0") or 1.0),
                   embargo=_env_bool("RAICING_EMBARGO", True),
                   rating_quick=_env_bool("RAICING_RATING_QUICK", False))


# ---------------------------------------------------------------------- constants

N_ROUNDS = 10
REFERENCE_BENCH = ("pro", "prudent")
SETUP_FIELDS = tuple(f.name for f in fields(Setup))
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

SYSTEM_TEAMS: list[tuple[str, str, str]] = [
    ("Boréal Racing", "#5DADE2", "prudent"),
    ("Scuderia Vulcain", "#E74C3C", "rapide"),
    ("Ondine Motorsport", "#1ABC9C", "pneus"),
    ("Équipe Mistral", "#F4D03F", "cooperatif"),
    ("Granit GP", "#95A5A6", "pro"),
    ("Lynx Compétition", "#AF7AC5", "debutant"),
    ("Ember Racing", "#E67E22", "pro"),
    ("Céleste Racing", "#85C1E9", "prudent"),
    ("Écurie Aurora", "#58D68D", "pneus"),
    ("Phénix Motorsport", "#C0392B", "rapide"),
    ("Sirocco Racing", "#D4AC0D", "cooperatif"),
    ("Givre GP", "#AED6F1", "pro"),
]

DRIVER_NAMES = [
    "Vega", "Orion", "Nova", "Kite", "Lyra", "Atlas", "Rigel", "Sirius", "Altaïr", "Deneb", "Électra", "Maïa",
    "Cassio", "Draco", "Hydra", "Mira", "Tycho", "Zéphyr", "Aube", "Givre", "Tison", "Brasier", "Nérée", "Corail",
    "Ombre", "Pixel", "Comète", "Éclair", "Spirale", "Bora", "Sillage", "Carène", "Halo", "Quasar", "Pulsar",
    "Nadir", "Zénith", "Écho", "Prisme", "Argos", "Titan", "Rhéa", "Phœbé", "Callisto", "Io", "Europa", "Ganymède",
    "Triton", "Obéron", "Ariel", "Miranda", "Talos", "Icare", "Dédale", "Persée", "Andromède", "Céphée", "Hélios",
]
DRIVER_NUMBERS = [7, 11, 22, 3, 5, 9, 14, 16, 18, 21, 23, 27, 31, 33, 44, 55, 63, 77, 81, 88, 4, 6, 8, 10, 12,
                  15, 17, 19, 20, 24, 25, 26, 28, 30, 99, 2, 32, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 45, 46]


def _category(track_length: float, lap_time: float) -> str:
    avg = track_length / max(lap_time, 1e-6)
    if avg > 52.0:
        return "rapide"
    if avg < 44.0:
        return "technique"
    return "mixte"


def _np_default(o: Any) -> Any:
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"{type(o).__name__} is not JSON serialisable")


def save_json_gz(obj: Any, path: Path) -> Path:
    """Strict JSON (no NaN) gzip file, readable by browsers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = json.dumps(obj, default=_np_default, allow_nan=False, separators=(",", ":"), ensure_ascii=False)
    except ValueError:
        text = json.dumps(to_jsonable(obj), separators=(",", ":"), ensure_ascii=False)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)
    return path


@dataclass
class _Car:
    """One car of a round, with its database identity."""

    entry: Entry
    team: Team
    driver: Driver
    brain_id: int | None
    setup: Setup
    explicit: bool


# ====================================================================== service


class LeagueService:
    """All league operations. ``data_dir`` holds brains, replays, live feeds and job work dirs."""

    def __init__(self, session: Session, data_dir: str | Path, options: ServiceOptions | None = None):
        self.s = session
        self.data_dir = Path(data_dir)
        self.opt = options or ServiceOptions.from_env()

    # ------------------------------------------------------------------ lookups
    def _league(self) -> League:
        lg = self.s.scalar(select(League).order_by(League.id).limit(1))
        if lg is None:
            raise NotFound("aucune ligue n'existe encore : lancez `stewards init`")
        return lg

    def has_league(self) -> bool:
        return self.s.scalar(select(func.count(League.id))) > 0

    def _league_seed(self) -> int:
        return int(self._league().settings.get("seed", 0))

    def _team(self, team_id: str) -> Team:
        t = self.s.get(Team, team_id)
        if t is None:
            raise NotFound(f"écurie inconnue : {team_id}")
        return t

    def _team_for_action(self, team_id: str) -> Team:
        t = self._team(team_id)
        if t.is_system:
            raise Forbidden("les écuries système sont gérées par les commissaires")
        return t

    def _driver(self, driver_id: str) -> Driver:
        d = self.s.get(Driver, driver_id)
        if d is None:
            raise NotFound(f"pilote inconnu : {driver_id}")
        return d

    def _own_driver(self, team_id: str, driver_id: str) -> Driver:
        d = self._driver(driver_id)
        if d.team_id != team_id:
            raise Forbidden(f"le pilote {driver_id} n'appartient pas à votre écurie")
        return d

    def _season(self, number: int) -> Season:
        s = self.s.get(Season, int(number))
        if s is None:
            raise NotFound(f"saison inconnue : {number}")
        return s

    def _round(self, round_id: int) -> Round:
        r = self.s.get(Round, int(round_id))
        if r is None:
            raise NotFound(f"manche inconnue : {round_id}")
        return r

    def _brain(self, brain_id: int) -> BrainRecord:
        b = self.s.get(BrainRecord, int(brain_id))
        if b is None:
            raise NotFound(f"cerveau inconnu : {brain_id}")
        return b

    def _own_brain(self, team_id: str, brain_id: int) -> BrainRecord:
        b = self._brain(brain_id)
        if b.team_id != team_id:
            raise Forbidden(f"le cerveau {brain_id} n'appartient pas à votre écurie")
        return b

    def _job(self, job_id: int) -> Job:
        j = self.s.get(Job, int(job_id))
        if j is None:
            raise NotFound(f"entraînement inconnu : {job_id}")
        return j

    def _seasons(self) -> list[Season]:
        return list(self.s.scalars(select(Season).order_by(Season.number)))

    def _active_season(self) -> Season | None:
        return self.s.scalar(select(Season).where(Season.status == "active").order_by(Season.number))

    def _current_season(self) -> Season | None:
        """Active season, else the next planned one, else the last finished one."""
        seasons = self._seasons()
        for st in ("active", "planned"):
            for s in seasons:
                if s.status == st:
                    return s
        return seasons[-1] if seasons else None

    def _open_season(self) -> Season:
        """Season in which garage and lab actions apply (active, or planned during the off-season)."""
        s = self._current_season()
        if s is None or s.status == "finished":
            raise Conflict("aucune saison en préparation ou en cours")
        return s

    @staticmethod
    def _regs(season: Season) -> Regulations:
        return Regulations.from_dict(season.regulations)

    def _rounds(self, season: int) -> list[Round]:
        return list(self.s.scalars(select(Round).where(Round.season == season).order_by(Round.number)))

    def _next_round(self, season: Season) -> Round | None:
        return self.s.scalar(select(Round).where(Round.season == season.number, Round.status != "done")
                             .order_by(Round.number).limit(1))

    def _teams(self) -> list[Team]:
        return list(self.s.scalars(select(Team).order_by(Team.is_system, Team.created_at, Team.id)))

    def _drivers_of(self, team_id: str) -> list[Driver]:
        return list(self.s.scalars(select(Driver).where(Driver.team_id == team_id).order_by(Driver.id)))

    def _hidden_round(self) -> int | None:
        """Round whose results are under embargo (its live broadcast is still running)."""
        if not self.opt.embargo:
            return None
        return embargoed_round(self.data_dir / "live")

    def _visible_done(self, rnd: Round, hidden: int | None) -> bool:
        return rnd.status == "done" and rnd.id != hidden

    # ================================================================== league & seasons
    def create_league(self, name: str, seed: int | None = None, n_rounds: int = N_ROUNDS) -> dict[str, Any]:
        """Create the league, its secret and season 1 (``planned``) with its rounds."""
        if self.has_league():
            raise Conflict("une ligue existe déjà dans cette base")
        if not 1 <= int(n_rounds) <= 30:
            raise LeagueError("le nombre de manches doit être compris entre 1 et 30")
        secret = sha256_hex(f"raicing-league-seed:{seed}") if seed is not None else secrets.token_hex(32)
        league = League(name=name, secret=secret,
                        settings={"seed": derived_seed(secret, "league", digits=8), "n_rounds": int(n_rounds),
                                  "bench": []})
        self.s.add(league)
        self.s.flush()
        self._create_season(1, Regulations(), None)
        self.s.commit()
        return self.get_league()

    def _create_season(self, number: int, regs: Regulations, change: dict[str, Any] | None) -> Season:
        league = self._league()
        seed = int(league.settings.get("seed", 0))
        season = Season(number=number, status="planned",
                        budget=int(economy.season_budget(number, seed, regs.economy)),
                        quota=int(regs.training.season_quota), regulations=to_jsonable(regs.to_dict()),
                        change=change)
        self.s.add(season)
        self.s.flush()
        for k in range(1, int(league.settings.get("n_rounds", N_ROUNDS)) + 1):
            track_seed = derived_seed(league.secret, number, k, "track")
            weather_seed = derived_seed(league.secret, number, k, "weather")
            track = generate_track(track_seed, regs.track)
            lap = estimate_lap_time(track, regs)
            self.s.add(Round(season=number, number=k, status="scheduled", track_seed=track_seed,
                             weather_seed=weather_seed, laps=race_laps(track, regs), track_name=track.name,
                             track_length=float(track.length), category=_category(track.length, lap),
                             rain_probability=float(regs.weather.rain_probability)))
        self.s.flush()
        return season

    def start_season(self, number: int) -> dict[str, Any]:
        season = self._season(number)
        if season.status != "planned":
            raise Conflict(f"la saison {number} n'est pas en préparation (statut : {season.status})")
        prev = self.s.get(Season, number - 1)
        if prev is not None and prev.status != "finished":
            raise Conflict(f"la saison {number - 1} doit d'abord être close")
        if self._active_season() is not None:
            raise Conflict("une autre saison est déjà en cours")
        season.status = "active"
        season.started_at = utcnow()
        for team in self._teams():
            self._ensure_garage(team.id, season)
        self.s.commit()
        return self.get_season(number)

    def schedule_rounds(self, number: int, start: datetime, every: timedelta) -> dict[str, Any]:
        """Give the not-yet-run rounds of a season a date (the worker runs them when due)."""
        season = self._season(number)
        if season.status == "finished":
            raise Conflict("saison terminée")
        k = 0
        for rnd in self._rounds(number):
            if rnd.status in ("scheduled", "entries_open"):
                rnd.scheduled_at = aware(start) + k * every
                k += 1
        self.s.commit()
        return self.get_season(number)

    def close_season(self, number: int) -> dict[str, Any]:
        """Finish a season, choose the regulation change on the historical bench, plan season n+1."""
        season = self._season(number)
        if season.status != "active":
            raise Conflict(f"la saison {number} n'est pas en cours (statut : {season.status})")
        left = [r.number for r in self._rounds(number) if r.status != "done"]
        if left:
            raise Conflict(f"il reste {len(left)} manche(s) à courir : {', '.join(map(str, left))}")
        regs = self._regs(season)
        league = self._league()
        standings = self._standings(number, hidden=None)["drivers"]
        bench_list = list(league.settings.get("bench", []))
        for row in standings[:2]:
            driver = self.s.get(Driver, row["driver_id"])
            if driver is None:
                continue
            team = self._team(driver.team_id)
            item: dict[str, Any] = {"season": number, "pos": row["pos"], "driver_id": driver.id}
            if team.is_system:
                item["reference"] = team.reference_personality or "pro"
            else:
                last = self.s.scalar(select(ResultRow.brain_id).join(Round, Round.id == ResultRow.round_id)
                                     .where(ResultRow.driver_id == driver.id, Round.season == number,
                                            ResultRow.brain_id.is_not(None))
                                     .order_by(Round.number.desc()).limit(1))
                item["brain_id"] = last or driver.active_brain_id
            bench_list.append(item)
        league.settings = {**league.settings, "bench": bench_list}
        pilots = self._bench_pilots(bench_list)
        change = regulations_mod.choose_change(regs, number + 1, int(league.settings.get("seed", 0)), pilots,
                                               attempts=self.opt.bench_attempts, run_bench=self.opt.run_bench)
        new_regs = regulations_mod.apply(regs, change)
        change_dict = to_jsonable(change.to_dict())
        if not self.opt.run_bench:
            change_dict["bench"] = {"skipped": True, "pilots": sorted(pilots)}
        season.status = "finished"
        season.finished_at = utcnow()
        self.s.add(RegulationChangeRecord(season=number + 1, path=change.path, label=change.label,
                                          old=float(change.old), new=float(change.new), percent=float(change.percent),
                                          affects=change.affects, rationale=change.rationale,
                                          bench=change_dict.get("bench") or {}))
        self._create_season(number + 1, new_regs, change_dict)
        self.s.commit()
        return {"season": number, "status": "finished", "next_season": number + 1,
                "change": self._change_public(change_dict, number + 1)}

    def _bench_pilots(self, bench_list: list[dict[str, Any]]) -> dict[str, Any]:
        pilots: dict[str, Any] = {f"ref:{p}": p for p in REFERENCE_BENCH}
        for item in bench_list:
            key = f"S{item['season']}P{item['pos']}:{item['driver_id']}"
            if item.get("reference"):
                pilots[key] = item["reference"]
            elif item.get("brain_id"):
                try:
                    pilots[key] = self.load_brain(int(item["brain_id"]))
                except (LeagueError, OSError, ValueError) as exc:
                    log.warning("bench pilot %s unavailable: %s", key, exc)
        return pilots

    # ================================================================== teams
    def create_team(self, name: str, color: str = "#888888", director: str = "", is_system: bool = False,
                    reference: str | None = None, driver_names: list[str] | None = None) -> dict[str, Any]:
        """Create a team with two drivers. The clear token is returned **once** (``token``)."""
        self._league()
        name = name.strip()
        if not name:
            raise LeagueError("nom d'écurie vide")
        if not COLOR_RE.match(color or ""):
            raise LeagueError("couleur invalide : format #RRGGBB attendu")
        if self.s.scalar(select(Team).where(Team.name == name)) is not None:
            raise Conflict(f"une écurie s'appelle déjà « {name} »")
        base = slugify(name)
        slug, k = base, 2
        while self.s.get(Team, slug) is not None:
            slug, k = f"{base}-{k}", k + 1
        token: str | None = None
        token_hash: str | None = None
        if is_system:
            reference = reference if reference in PERSONALITIES else "pro"
        else:
            reference = None
            token = "rai_" + secrets.token_urlsafe(32)
            token_hash = sha256_hex(token)
        team = Team(id=slug, name=name, color=color.upper(), director=director, token_hash=token_hash,
                    is_system=is_system, reference_personality=reference)
        self.s.add(team)
        used_names = set(self.s.scalars(select(Driver.name)))
        used_numbers = set(self.s.scalars(select(Driver.number)))
        names = list(driver_names or [])
        start = int(sha256_hex(slug)[:6], 16)
        for k in range(2):
            if k < len(names) and names[k].strip():
                dname = names[k].strip()
            else:
                dname = next((DRIVER_NAMES[(start + j) % len(DRIVER_NAMES)] for j in range(len(DRIVER_NAMES))
                              if DRIVER_NAMES[(start + j) % len(DRIVER_NAMES)] not in used_names), f"Pilote {k + 1}")
            used_names.add(dname)
            number = next((n for n in DRIVER_NUMBERS if n not in used_numbers),
                          max(used_numbers | {1}) + 1)
            used_numbers.add(number)
            self.s.add(Driver(id=f"{slug}-{k + 1}", team_id=slug, name=dname, number=number))
        self.s.flush()
        season = self._current_season()
        if season is not None and season.status != "finished":
            self._ensure_garage(slug, season)
        self.s.commit()
        return {"team": self.get_team(slug), "token": token}

    def ensure_system_teams(self, n_total: int = 8) -> list[str]:
        """Fill the grid with system teams (reference pilots) up to ``n_total`` teams."""
        created: list[str] = []
        existing = {t.name for t in self._teams()}
        count = len(existing)
        k = 0
        while count < n_total:
            if k < len(SYSTEM_TEAMS):
                name, color, pers = SYSTEM_TEAMS[k]
            else:
                name, color, pers = f"Écurie système {k + 1}", "#888888", list(PERSONALITIES)[k % len(PERSONALITIES)]
            k += 1
            if name in existing:
                continue
            out = self.create_team(name, color, "Commissaires (système)", is_system=True, reference=pers)
            created.append(out["team"]["id"])
            existing.add(name)
            count += 1
        return created

    def rotate_token(self, team_id: str) -> dict[str, Any]:
        team = self._team_for_action(team_id)
        token = "rai_" + secrets.token_urlsafe(32)
        team.token_hash = sha256_hex(token)
        self.s.commit()
        return {"team_id": team.id, "token": token}

    def authenticate(self, token: str | None) -> str:
        """Return the team id owning ``token`` (raises :class:`Unauthorized`)."""
        if not token:
            raise Unauthorized("jeton d'écurie manquant")
        team = self.s.scalar(select(Team).where(Team.token_hash == sha256_hex(token.strip())))
        if team is None or team.is_system:
            raise Unauthorized("jeton d'écurie invalide")
        return team.id

    # ================================================================== garage
    def _ensure_garage(self, team_id: str, season: Season) -> Garage:
        row = self.s.get(Garage, (team_id, season.number))
        if row is not None:
            return row
        prev = self.s.scalar(select(Garage).where(Garage.team_id == team_id, Garage.season < season.number)
                             .order_by(Garage.season.desc()).limit(1))
        regs = self._regs(season)
        if prev is not None:
            affected: list[str] = []
            for ch in self.s.scalars(select(RegulationChangeRecord).where(
                    RegulationChangeRecord.season > prev.season, RegulationChangeRecord.season <= season.number)):
                if ch.affects:
                    affected.append(ch.affects)
            state = economy.new_season(economy.GarageState.from_dict(prev.state), season.number, season.budget,
                                       regs.economy, affected)
        else:
            state = economy.GarageState(season=season.number, budget=float(season.budget))
        row = Garage(team_id=team_id, season=season.number, state=to_jsonable(state.to_dict()))
        self.s.add(row)
        self.s.flush()
        team = self._team(team_id)
        if team.is_system:
            self._auto_build(row, regs)
        return row

    def _auto_build(self, row: Garage, regs: Regulations) -> None:
        """Balanced, deterministic build for system teams: revise, then level parts evenly."""
        g = economy.GarageState.from_dict(row.state)
        eco = regs.economy
        txs: list[tuple[str, str, float]] = []
        for part in list(g.non_compliant):
            try:
                txs.append(("revision", part, economy.revise(g, part)))
            except economy.GarageError:
                pass
        priority = ["aero", "engine", "suspension", "brakes", "bodywork", "cooling", "chassis"]
        while True:
            options = []
            for p in priority:
                lv = g.parts[p]
                if lv >= eco.max_level or p in g.non_compliant:
                    continue
                if economy.upgrade_cost(p, lv, eco) > g.available:
                    continue
                nxt = dict(g.parts)
                nxt[p] = lv + 1
                if economy.check_requirements(g, nxt):
                    continue
                options.append((lv, priority.index(p), p))
            if not options:
                break
            part = min(options)[2]
            txs.append(("construction", f"{part}:{g.parts[part] + 1}", economy.upgrade(g, part, eco)))
        row.state = to_jsonable(g.to_dict())
        for kind, item, cost in txs:
            self.s.add(GarageTx(team_id=row.team_id, season=row.season, kind=kind, item=item, cost=float(cost)))

    def _garage(self, team_id: str) -> tuple[Season, Garage, economy.GarageState]:
        season = self._open_season()
        row = self._ensure_garage(team_id, season)
        return season, row, economy.GarageState.from_dict(row.state)

    @staticmethod
    def _garage_public(row: Garage | None) -> dict[str, Any] | None:
        if row is None:
            return None
        g = economy.GarageState.from_dict(row.state)
        return {"season": row.season, "budget": g.budget, "spent": g.spent, "available": g.available,
                "parts": dict(g.parts), "technologies": list(g.technologies),
                "capacity": economy.capacity(g), "non_compliant": dict(g.non_compliant)}

    def get_catalog(self, team_id: str) -> dict[str, Any]:
        self._team_for_action(team_id)
        season, row, g = self._garage(team_id)
        self.s.commit()   # the garage may just have been created (season roll-over)
        out = economy.catalog(g, self._regs(season).economy)
        out["season"] = season.number
        out["requirements"] = [{"part": p, "from_level": lv, "requires": req} for p, lv, req in economy.REQUIREMENTS]
        out["non_compliant"] = dict(g.non_compliant)
        return to_jsonable(out)

    def _garage_action(self, team_id: str, kind: str, item: str) -> dict[str, Any]:
        self._team_for_action(team_id)
        season, row, g = self._garage(team_id)
        rules = self._regs(season).economy
        try:
            if kind == "construction":
                cost = economy.upgrade(g, item, rules)
                label = f"{item}:{g.parts[item]}"
            elif kind == "research":
                cost = economy.research(g, item)
                label = item
            else:
                cost = economy.revise(g, item)
                label = item
        except economy.GarageError as exc:
            self.s.rollback()
            raise LeagueError(str(exc)) from exc
        row.state = to_jsonable(g.to_dict())
        self.s.add(GarageTx(team_id=team_id, season=season.number, kind=kind, item=label, cost=float(cost)))
        self.s.commit()
        return {"ok": True, "kind": kind, "item": label, "cost": cost, "garage": self._garage_public(row)}

    def upgrade_part(self, team_id: str, part: str) -> dict[str, Any]:
        return self._garage_action(team_id, "construction", part)

    def research_technology(self, team_id: str, technology: str) -> dict[str, Any]:
        return self._garage_action(team_id, "research", technology)

    def revise_part(self, team_id: str, part: str) -> dict[str, Any]:
        return self._garage_action(team_id, "revision", part)

    # ================================================================== brains
    def brain_dir(self, rec: BrainRecord) -> Path:
        return self.data_dir / rec.path

    def load_brain(self, brain_id: int) -> Brain:
        rec = self._brain(brain_id)
        try:
            return Brain.load(self.brain_dir(rec))
        except OSError as exc:
            raise NotFound(f"fichiers du cerveau {brain_id} introuvables") from exc

    @staticmethod
    def _architecture(brain: Brain) -> dict[str, Any]:
        kinds: list[str] = []
        hidden: list[int] = []
        gru = 0

        def walk(layers: list[dict[str, Any]]) -> None:
            nonlocal gru
            for layer in layers:
                kinds.append(layer["type"])
                if layer["type"] == "linear":
                    hidden.append(int(brain.weights[layer["w"]].shape[1]))
                elif layer["type"] == "gru":
                    gru = int(layer["hidden"])
                    hidden.append(gru)
                elif layer["type"] == "residual":
                    walk(layer.get("layers", []))
        walk(brain.spec.get("layers", []))
        return {"hidden": hidden[:-1], "layers": kinds, "gru": gru, "output": brain.spec.get("output", {}),
                "obs_norm": bool(brain.spec.get("obs_norm")), "n_layers": len(kinds)}

    def register_brain(self, team_id: str, driver_id: str, brain: Brain, method: str = "unknown",
                       parent_id: int | None = None, training_steps: int = 0, imported: bool = False,
                       meta: dict[str, Any] | None = None, activate: bool = False,
                       commit: bool = True) -> dict[str, Any]:
        """Store a new generation of ``driver_id`` (generation = max + 1) and save its files."""
        driver = self._own_driver(team_id, driver_id)
        season = self._current_season()
        regs = self._regs(season) if season is not None else Regulations()
        try:
            brain.validate(regs.training.max_brain_params, regs.training.max_brain_layers)
        except (BrainError, KeyError, TypeError, ValueError) as exc:
            raise LeagueError(f"cerveau invalide : {exc}") from exc
        if parent_id is not None:
            self._own_brain(team_id, parent_id)
        gen = (self.s.scalar(select(func.max(BrainRecord.generation)).where(BrainRecord.driver_id == driver_id))
               or 0) + 1
        brain.meta.update(to_jsonable(meta or {}))
        brain.meta.update({"team_id": team_id, "driver_id": driver_id, "generation": gen, "method": method})
        rec = BrainRecord(team_id=team_id, driver_id=driver_id, generation=gen, method=method,
                          params=brain.param_count(), architecture=to_jsonable(self._architecture(brain)),
                          parent_id=parent_id, training_steps=int(training_steps), path="",
                          meta=to_jsonable(brain.meta), imported=imported)
        self.s.add(rec)
        self.s.flush()
        rec.path = f"brains/{rec.id}"
        brain.save(self.data_dir / rec.path)
        if activate:
            self._set_active(team_id, driver, rec.id)
        if commit:
            self.s.commit()
        return self._brain_dict(rec)

    def import_brain(self, team_id: str, driver_id: str, brain: Brain, method: str = "external") -> dict[str, Any]:
        """Register a brain built outside the league lab (only if the regulations allow it)."""
        self._team_for_action(team_id)
        season = self._open_season()
        if not self._regs(season).training.allow_external_brains:
            raise Forbidden("le règlement interdit les cerveaux externes : les cerveaux doivent sortir du "
                            "laboratoire de la ligue (paddock lab train)")
        return self.register_brain(team_id, driver_id, brain, method=method, imported=True,
                                   meta={"imported": True})

    def _set_active(self, team_id: str, driver: Driver, brain_id: int) -> None:
        for mate in self._drivers_of(team_id):
            if mate.id != driver.id and mate.active_brain_id == brain_id:
                raise Conflict(f"le cerveau {brain_id} est déjà le cerveau actif de {mate.id} : "
                               "les deux pilotes d'une écurie doivent utiliser des cerveaux distincts")
        driver.active_brain_id = brain_id

    def activate_brain(self, team_id: str, driver_id: str, brain_id: int) -> dict[str, Any]:
        self._team_for_action(team_id)
        driver = self._own_driver(team_id, driver_id)
        self._own_brain(team_id, brain_id)
        self._set_active(team_id, driver, int(brain_id))
        self.s.commit()
        return {"driver_id": driver.id, "active_brain": self._brain_dict(self._brain(brain_id))}

    def _latest_rating(self, brain_id: int) -> RatingRecord | None:
        return self.s.scalar(select(RatingRecord).where(RatingRecord.brain_id == brain_id)
                             .order_by(RatingRecord.created_at.desc(), RatingRecord.id.desc()).limit(1))

    def _brain_dict(self, rec: BrainRecord, full: bool = False) -> dict[str, Any]:
        driver = self.s.get(Driver, rec.driver_id)
        rating = self._latest_rating(rec.id)
        out: dict[str, Any] = {
            "id": rec.id, "team_id": rec.team_id, "driver_id": rec.driver_id, "generation": rec.generation,
            "method": rec.method, "params": rec.params, "training_steps": rec.training_steps,
            "parent": rec.parent_id, "imported": rec.imported, "created_at": iso(rec.created_at),
            "active_for": [d.id for d in self._drivers_of(rec.team_id) if d.active_brain_id == rec.id],
            "rating": round(rating.overall, 1) if rating else None,
        }
        if driver is not None:
            out["driver"] = driver.name
        if full:
            out["architecture"] = rec.architecture
            out["meta"] = rec.meta
            out["ratings"] = [self._rating_dict(r) for r in self.s.scalars(
                select(RatingRecord).where(RatingRecord.brain_id == rec.id).order_by(RatingRecord.id))]
        return out

    def list_brains(self, team_id: str) -> list[dict[str, Any]]:
        self._team(team_id)
        recs = self.s.scalars(select(BrainRecord).where(BrainRecord.team_id == team_id)
                              .order_by(BrainRecord.driver_id, BrainRecord.generation))
        return [self._brain_dict(r) for r in recs]

    def get_brain(self, team_id: str, brain_id: int) -> dict[str, Any]:
        return self._brain_dict(self._own_brain(team_id, brain_id), full=True)

    def export_brain(self, team_id: str, brain_id: int) -> bytes:
        """Brain package (zip with brain.json + weights.npz) of one of the team's own brains."""
        self._own_brain(team_id, brain_id)
        return self.load_brain(brain_id).to_bytes()

    # ================================================================== ratings
    @staticmethod
    def _rating_dict(r: RatingRecord) -> dict[str, Any]:
        return {"brain_id": r.brain_id, "season": r.season, "overall": round(r.overall, 1),
                "ci": [round(r.ci_low, 1), round(r.ci_high, 1)],
                "components": {k: round(float(v), 1) for k, v in (r.components or {}).items()},
                "created_at": iso(r.created_at)}

    def rate_brain(self, brain_id: int, quick: bool | None = None) -> dict[str, Any]:
        """Run the Driver Rating now (blocking) and store it."""
        rec = self._brain(brain_id)
        season = self._current_season()
        regs = self._regs(season) if season is not None else Regulations()
        quick = self.opt.rating_quick if quick is None else quick
        cfg = rating_mod.RatingConfig.quick() if quick else rating_mod.RatingConfig()
        result = rating_mod.rate(self.load_brain(rec.id), regs, cfg)
        row = RatingRecord(brain_id=rec.id, season=season.number if season else 1, overall=float(result.overall),
                           ci_low=float(result.ci[0]), ci_high=float(result.ci[1]),
                           components=to_jsonable(result.components))
        self.s.add(row)
        self.s.commit()
        return self._rating_dict(row)

    def request_rating(self, team_id: str, brain_id: int, quick: bool | None = None) -> dict[str, Any]:
        """Queue a Driver Rating for the worker (server mode). Ratings do not use the quota."""
        self._team_for_action(team_id)
        rec = self._own_brain(team_id, brain_id)
        pending = self.s.scalars(select(Job).where(Job.team_id == team_id, Job.status.in_(("queued", "running"))))
        for j in pending:
            if j.config.get("kind") == "rating" and int(j.config.get("brain_id", -1)) == rec.id:
                return self._job_dict(j)
        job = Job(team_id=team_id, driver_id=rec.driver_id, status="queued",
                  config={"kind": "rating", "brain_id": rec.id,
                          "quick": self.opt.rating_quick if quick is None else bool(quick)},
                  steps_requested=0, steps_used=0, progress={})
        self.s.add(job)
        self.s.commit()
        return self._job_dict(job)

    # ================================================================== lab jobs
    def quota_used(self, team_id: str, season: int) -> int:
        return int(self.s.scalar(select(func.coalesce(func.sum(QuotaLedger.steps), 0))
                                 .where(QuotaLedger.team_id == team_id, QuotaLedger.season == season)) or 0)

    def _quota(self, team_id: str, season: Season | None) -> dict[str, Any] | None:
        if season is None:
            return None
        used = self.quota_used(team_id, season.number)
        return {"season": season.number, "used": used, "total": int(season.quota),
                "remaining": max(int(season.quota) - used, 0)}

    def _job_dict(self, j: Job) -> dict[str, Any]:
        cfg = j.config or {}
        return {"id": j.id, "team_id": j.team_id, "driver_id": j.driver_id, "kind": cfg.get("kind", "training"),
                "method": cfg.get("method"), "status": j.status, "steps_requested": j.steps_requested,
                "steps_used": j.steps_used, "progress": j.progress or {}, "result_brain_id": j.result_brain_id,
                "error": j.error, "created_at": iso(j.created_at), "started_at": iso(j.started_at),
                "finished_at": iso(j.finished_at), "config": cfg}

    def create_job(self, team_id: str, config: dict[str, Any]) -> dict[str, Any]:
        """Validate a training configuration (docs/LAB.md) and queue it."""
        self._team_for_action(team_id)
        season = self._open_season()
        regs = self._regs(season)
        try:
            cfg = training.validate_config(dict(config))
        except training.TrainingUnavailable as exc:
            raise LeagueError(str(exc), 503) from exc
        except Exception as exc:  # the lab's validator raises its own error types
            raise LeagueError(f"configuration d'entraînement invalide : {exc}") from exc
        cfg = to_jsonable(cfg)
        cfg["kind"] = "training"
        self._own_driver(team_id, cfg["driver_id"])
        if cfg.get("init_from") is not None:
            cfg["init_from"] = self._brain_ref(team_id, cfg["init_from"], "init_from")
        for opp in (cfg.get("curriculum") or {}).get("opponents") or []:
            if str(opp) not in PERSONALITIES:
                self._brain_ref(team_id, opp, "curriculum.opponents")
        if self.quota_used(team_id, season.number) >= season.quota:
            raise Conflict(f"quota d'entraînement épuisé pour la saison {season.number}")
        running = [j for j in self.s.scalars(select(Job).where(Job.team_id == team_id,
                                                                  Job.status.in_(("queued", "running"))))
                   if (j.config or {}).get("kind", "training") == "training"]
        if len(running) >= regs.training.max_concurrent_jobs:
            raise Conflict(f"au plus {regs.training.max_concurrent_jobs} entraînements simultanés "
                           "(en file ou en cours)")
        job = Job(team_id=team_id, driver_id=cfg["driver_id"], status="queued", config=cfg,
                  steps_requested=int(cfg["steps"]), steps_used=0, progress={})
        self.s.add(job)
        self.s.commit()
        return self._job_dict(job)

    def list_jobs(self, team_id: str) -> list[dict[str, Any]]:
        self._team(team_id)
        return [self._job_dict(j) for j in self.s.scalars(select(Job).where(Job.team_id == team_id)
                                                           .order_by(Job.id.desc()))]

    def get_job(self, team_id: str, job_id: int) -> dict[str, Any]:
        j = self._job(job_id)
        if j.team_id != team_id:
            raise Forbidden("cet entraînement n'appartient pas à votre écurie")
        return self._job_dict(j)

    def cancel_job(self, team_id: str, job_id: int) -> dict[str, Any]:
        j = self._job(job_id)
        if j.team_id != team_id:
            raise Forbidden("cet entraînement n'appartient pas à votre écurie")
        if j.status not in ("queued", "running"):
            raise Conflict(f"entraînement déjà terminé (statut : {j.status})")
        was = j.status
        j.status = "cancelled"
        if was == "queued":
            j.finished_at = utcnow()
        self.s.commit()
        return self._job_dict(j)

    def next_queued_job(self) -> int | None:
        return self.s.scalar(select(Job.id).where(Job.status == "queued").order_by(Job.id).limit(1))

    def _claim_job(self, job_id: int) -> bool:
        res = self.s.execute(update(Job).where(Job.id == job_id, Job.status == "queued")
                             .values(status="running", started_at=utcnow()))
        self.s.commit()
        return res.rowcount == 1

    def _job_status(self, job_id: int) -> str:
        self.s.commit()   # end the current transaction to see other processes' writes
        return str(self.s.scalar(select(Job.status).where(Job.id == job_id)))

    def run_job_now(self, job_id: int) -> dict[str, Any]:
        """Execute a queued job in this process (worker, or local-mode CLI)."""
        job = self._job(job_id)
        if job.status != "queued" or not self._claim_job(job.id):
            raise Conflict(f"l'entraînement {job_id} n'est pas en file d'attente")
        self.s.refresh(job)
        if (job.config or {}).get("kind") == "rating":
            return self._run_rating_job(job)
        return self._run_training_job(job)

    def _run_rating_job(self, job: Job) -> dict[str, Any]:
        try:
            rating = self.rate_brain(int(job.config["brain_id"]), quick=bool(job.config.get("quick")))
            job.status = "done"
            job.progress = {"rating": rating}
        except Exception as exc:  # report any failure on the job
            self.s.rollback()
            job = self._job(job.id)
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            log.exception("rating job %s failed", job.id)
        job.finished_at = utcnow()
        self.s.commit()
        return self._job_dict(job)

    def _run_training_job(self, job: Job) -> dict[str, Any]:
        cfg = dict(job.config)
        team_id = job.team_id
        season = self._open_season()
        regs = self._regs(season)
        remaining = max(int(season.quota) - self.quota_used(team_id, season.number), 0)
        workdir = self.data_dir / "jobs" / str(job.id)
        workdir.mkdir(parents=True, exist_ok=True)
        last: dict[str, Any] = {}
        clock = {"commit": 0.0, "poll": 0.0, "stop": False}

        def progress(info: dict[str, Any]) -> None:
            last.update(to_jsonable(info))
            now = time.monotonic()
            if now - clock["commit"] >= 2.0:
                clock["commit"] = now
                job.progress = dict(last)
                self.s.commit()

        def should_stop() -> bool:
            now = time.monotonic()
            if not clock["stop"] and now - clock["poll"] >= 3.0:
                clock["poll"] = now
                clock["stop"] = self._job_status(job.id) == "cancelled"
            return bool(clock["stop"])

        try:
            init = self.load_brain(int(cfg["init_from"])) if cfg.get("init_from") is not None else None
            if init is not None and self._brain(int(cfg["init_from"])).team_id != team_id:
                raise Forbidden("init_from : cerveau d'une autre écurie")
            result = training.run_job(config=training.lab_config(cfg), regs=regs, init_brain=init,
                                      quota_remaining=remaining, workdir=workdir, progress=progress,
                                      should_stop=should_stop, brain_resolver=self._resolver(team_id))
            steps = min(int(getattr(result, "steps_used", 0)), remaining)
            cancelled = self._job_status(job.id) == "cancelled"
            self._ledger(team_id, season.number, steps, job.id)
            brain_info = self.register_brain(
                team_id, cfg["driver_id"], result.brain, method=str(cfg.get("method", "unknown")),
                parent_id=int(cfg["init_from"]) if cfg.get("init_from") is not None else None,
                training_steps=steps, meta={"job_id": job.id, "eval": getattr(result, "eval", {}) or {},
                                            "cancelled": cancelled},
                activate=bool(cfg.get("activate")) and not cancelled, commit=False)
            job.status = "cancelled" if cancelled else "done"
            job.steps_used = steps
            job.result_brain_id = brain_info["id"]
            job.progress = {**last, "steps": steps, "eval": to_jsonable(getattr(result, "eval", {}) or {})}
        except Exception as exc:  # any training failure is reported on the job
            self.s.rollback()
            job = self._job(job.id)
            steps = min(int(last.get("steps", 0) or 0), remaining)
            if steps:
                self._ledger(team_id, season.number, steps, job.id)
            job.status = "cancelled" if self._job_status(job.id) == "cancelled" else "failed"
            job.steps_used = steps
            job.error = f"{type(exc).__name__}: {exc}"
            job.progress = dict(last)
            log.warning("training job %s failed: %s", job.id, exc)
        job.finished_at = utcnow()
        self.s.commit()
        return self._job_dict(job)

    def _brain_ref(self, team_id: str, ref: Any, where: str) -> int:
        """A brain id given in a training configuration: must be one of the team's own brains."""
        try:
            bid = int(str(ref).strip().lstrip("#"))
        except ValueError as exc:
            raise LeagueError(f"{where} : {ref!r} n'est ni une personnalité de référence "
                              f"({', '.join(PERSONALITIES)}) ni un identifiant de cerveau") from exc
        self._own_brain(team_id, bid)
        return bid

    def _resolver(self, team_id: str):
        """Frozen-brain opponents of traffic training: only the team's own brains."""
        def resolve(ref: str) -> Brain:
            return self.load_brain(self._brain_ref(team_id, ref, "curriculum.opponents"))
        return resolve

    def _ledger(self, team_id: str, season: int, steps: int, job_id: int | None) -> None:
        if steps > 0:
            self.s.add(QuotaLedger(team_id=team_id, season=season, steps=int(steps), job_id=job_id))

    # ================================================================== entries
    def _last_setup(self, driver_id: str) -> Setup:
        row = self.s.scalar(select(EntryRecord).where(EntryRecord.driver_id == driver_id)
                            .order_by(EntryRecord.round_id.desc()).limit(1))
        if row is None or not row.setup:
            return Setup()
        return Setup(**{k: v for k, v in row.setup.items() if k in SETUP_FIELDS}).clamp()

    @staticmethod
    def _parse_setup(base: Setup, setup: dict[str, Any] | None) -> Setup:
        values = asdict(base)
        for k, v in (setup or {}).items():
            if k not in SETUP_FIELDS:
                raise LeagueError(f"réglage inconnu : {k} (attendus : {', '.join(SETUP_FIELDS)})")
            if v is None:
                continue
            if k in ("wing", "brake_bias"):
                try:
                    values[k] = float(v)
                except (TypeError, ValueError) as exc:
                    raise LeagueError(f"réglage {k} : nombre attendu") from exc
            else:
                if v not in COMPOUNDS:
                    raise LeagueError(f"réglage {k} : gomme inconnue {v!r} (attendues : {', '.join(COMPOUNDS)})")
                values[k] = v
        return Setup(**values).clamp()

    def submit_entry(self, team_id: str, round_id: int, driver_id: str, brain_id: int | None = None,
                     setup: dict[str, Any] | None = None) -> dict[str, Any]:
        """Register a driver for a round with a brain of the team and a pre-race setup (clamped)."""
        self._team_for_action(team_id)
        rnd = self._round(round_id)
        season = self._season(rnd.season)
        if season.status != "active":
            raise Conflict("la saison de cette manche n'est pas en cours")
        if rnd.status not in ("scheduled", "entries_open"):
            raise Conflict(f"inscriptions closes pour cette manche (statut : {rnd.status})")
        nxt = self._next_round(season)
        if rnd.status == "scheduled" and (nxt is None or nxt.id != rnd.id):
            raise Conflict("les inscriptions ne sont ouvertes que pour la prochaine manche")
        driver = self._own_driver(team_id, driver_id)
        if brain_id is None:
            brain_id = driver.active_brain_id
            if brain_id is None:
                raise LeagueError(f"{driver_id} n'a pas de cerveau actif : précisez --brain")
        self._own_brain(team_id, brain_id)
        for mate in self._drivers_of(team_id):
            if mate.id == driver.id:
                continue
            mate_entry = self.s.scalar(select(EntryRecord).where(EntryRecord.round_id == rnd.id,
                                                                 EntryRecord.driver_id == mate.id))
            mate_brain = mate_entry.brain_id if mate_entry is not None else mate.active_brain_id
            if mate_brain == int(brain_id):
                raise Conflict(f"le cerveau {brain_id} est déjà engagé pour {mate.id} : "
                               "les deux pilotes d'une écurie doivent utiliser des cerveaux distincts")
        st = self._parse_setup(self._last_setup(driver.id), setup)
        row = self.s.scalar(select(EntryRecord).where(EntryRecord.round_id == rnd.id,
                                                      EntryRecord.driver_id == driver.id))
        if row is None:
            row = EntryRecord(round_id=rnd.id, team_id=team_id, driver_id=driver.id)
            self.s.add(row)
        row.brain_id = int(brain_id)
        row.setup = asdict(st)
        row.submitted_at = utcnow()
        self.s.commit()
        return {"round_id": rnd.id, "season": rnd.season, "round": rnd.number, "driver_id": driver.id,
                "entry_id": self._entry_id(driver), "brain_id": row.brain_id, "setup": row.setup,
                "submitted_at": iso(row.submitted_at)}

    @staticmethod
    def _entry_id(driver: Driver) -> str:
        team, _, k = driver.id.rpartition("-")
        return f"{team}#{k}"

    # ================================================================== rounds
    def open_entries(self, round_id: int) -> dict[str, Any]:
        rnd = self._round(round_id)
        season = self._season(rnd.season)
        if season.status != "active":
            raise Conflict("la saison de cette manche n'est pas en cours")
        if rnd.status != "scheduled":
            raise Conflict(f"impossible d'ouvrir les inscriptions (statut : {rnd.status})")
        rnd.status = "entries_open"
        self.s.commit()
        return self.get_round(rnd.id)

    def run_next(self) -> dict[str, Any]:
        """Open (if needed) and run the next round of the active season."""
        season = self._active_season()
        if season is None:
            raise Conflict("aucune saison en cours")
        rnd = self._next_round(season)
        if rnd is None:
            raise Conflict(f"toutes les manches de la saison {season.number} sont courues : "
                           f"close la saison avec `stewards season close {season.number}`")
        return self.run_round(rnd.id)

    def request_run(self, round_id: int) -> dict[str, Any]:
        """Ask the worker to run the next round as soon as possible (sets its date to now)."""
        rnd = self._round(round_id)
        season = self._season(rnd.season)
        if season.status != "active":
            raise Conflict("la saison de cette manche n'est pas en cours")
        nxt = self._next_round(season)
        if nxt is None or nxt.id != rnd.id or rnd.status not in ("scheduled", "entries_open"):
            raise Conflict("seule la prochaine manche, pas encore lancée, peut être programmée")
        rnd.status = "entries_open"
        rnd.scheduled_at = utcnow()
        self.s.commit()
        return self.get_round(rnd.id)

    def tick(self, now: datetime | None = None) -> list[int]:
        """Worker heartbeat: open the next round's entries and run it once its date has passed."""
        now = aware(now) or utcnow()
        season = self._active_season()
        if season is None:
            return []
        rnd = self._next_round(season)
        if rnd is None:
            return []
        if rnd.status == "scheduled":
            rnd.status = "entries_open"
            self.s.commit()
        if rnd.status == "entries_open" and rnd.scheduled_at is not None and aware(rnd.scheduled_at) <= now:
            self.run_round(rnd.id)
            return [rnd.id]
        return []

    def _championship_rank(self, season: int, before_number: int) -> dict[str, int]:
        st = self._standings(season, hidden=None, before_number=before_number)["drivers"]
        return {row["driver_id"]: row["pos"] for row in st}

    def _build_cars(self, rnd: Round, season: Season, regs: Regulations) -> tuple[list[_Car], list[dict[str, Any]]]:
        cars: list[_Car] = []
        forfeits: list[dict[str, Any]] = []
        for team in self._teams():
            garage = self._ensure_garage(team.id, season)
            mods = economy.modifiers(economy.GarageState.from_dict(garage.state))
            used_brains: set[int] = set()
            drivers = self._drivers_of(team.id)
            explicit = {e.driver_id: e for e in self.s.scalars(
                select(EntryRecord).where(EntryRecord.round_id == rnd.id, EntryRecord.team_id == team.id))}
            # explicit entries first so that a conflicting default entry is the one dropped
            drivers.sort(key=lambda d: (d.id not in explicit or explicit[d.id].submitted_at is None, d.id))
            for d in drivers:
                ent = explicit.get(d.id)
                is_explicit = ent is not None and ent.submitted_at is not None
                if team.is_system:
                    setup = Setup()
                    e = Entry(self._entry_id(d), team.id, team.name, d.name,
                              reference=team.reference_personality or "pro", setup=setup,
                              spec=build_spec(regs, mods, setup), color=team.color, driver_id=d.id)
                    cars.append(_Car(e, team, d, None, setup, False))
                    continue
                brain_id = ent.brain_id if is_explicit and ent is not None else d.active_brain_id
                if brain_id is None:
                    forfeits.append({"driver_id": d.id, "team_id": team.id, "reason": "aucun cerveau actif"})
                    continue
                if brain_id in used_brains:
                    forfeits.append({"driver_id": d.id, "team_id": team.id,
                                     "reason": f"cerveau {brain_id} déjà engagé par le coéquipier"})
                    continue
                if is_explicit and ent is not None:
                    setup = Setup(**{k: v for k, v in ent.setup.items() if k in SETUP_FIELDS}).clamp()
                else:
                    setup = self._last_setup(d.id)
                try:
                    rec = self._brain(brain_id)
                    brain = self.load_brain(brain_id).validate(regs.training.max_brain_params,
                                                               regs.training.max_brain_layers)
                except (LeagueError, BrainError, KeyError, ValueError) as exc:
                    forfeits.append({"driver_id": d.id, "team_id": team.id, "reason": f"cerveau invalide : {exc}"})
                    continue
                used_brains.add(brain_id)
                e = Entry(self._entry_id(d), team.id, team.name, d.name, brain=brain, setup=setup,
                          spec=build_spec(regs, mods, setup), color=team.color, driver_id=d.id,
                          generation=rec.generation)
                cars.append(_Car(e, team, d, brain_id, setup, is_explicit))
        cars.sort(key=lambda c: c.entry.entry_id)
        return cars, forfeits

    def run_round(self, round_id: int) -> dict[str, Any]:
        """Qualifying + race of a round; persist everything and publish the circuit."""
        rnd = self._round(round_id)
        season = self._season(rnd.season)
        if season.status != "active":
            raise Conflict("la saison de cette manche n'est pas en cours")
        if rnd.status not in ("scheduled", "entries_open"):
            raise Conflict(f"cette manche ne peut pas être lancée (statut : {rnd.status})")
        nxt = self._next_round(season)
        if nxt is not None and nxt.id != rnd.id:
            raise Conflict(f"la manche {nxt.number} doit être courue avant la manche {rnd.number}")
        regs = self._regs(season)
        cars, forfeits = self._build_cars(rnd, season, regs)
        if not cars:
            raise Conflict("aucune voiture engagée")
        previous_status = rnd.status
        res = self.s.execute(update(Round).where(Round.id == rnd.id, Round.status == previous_status)
                             .values(status="running"))
        self.s.commit()
        if res.rowcount != 1:
            raise Conflict("cette manche est déjà lancée")
        publisher: LivePublisher | None = None
        try:
            publisher = LivePublisher(self.data_dir / "live", self.opt.live_speed) if self.opt.live else None
            self._race(rnd, season, regs, cars, forfeits, publisher)
        except Exception as exc:
            self.s.rollback()
            if publisher is not None:
                publisher.finish(aborted=f"{type(exc).__name__}")
            self.s.execute(update(Round).where(Round.id == rnd.id).values(status=previous_status))
            self.s.commit()
            raise
        return self.get_round(rnd.id, embargo=False)

    def _race(self, rnd: Round, season: Season, regs: Regulations, cars: list[_Car],
              forfeits: list[dict[str, Any]], publisher: LivePublisher | None) -> None:
        track = generate_track(int(rnd.track_seed), regs.track)
        laps = int(self.opt.laps_override or rnd.laps)
        lap_est = estimate_lap_time(track, regs)
        # the weather script always covers the nominal race (a laps override only shortens the race)
        weather = generate_weather(int(rnd.weather_seed), max(int(rnd.laps), laps) * lap_est, regs.weather)
        wseed = int(rnd.weather_seed) % (2 ** 31 - 1)
        entries = [c.entry for c in cars]
        quali = run_qualifying(track, entries, regs, weather, seed=wseed, record=True,
                               time_limit=self.opt.quali_time_limit)
        # grid: timed cars by lap time, then cars without time in championship order
        rank = self._championship_rank(season.number, rnd.number)
        timed = [r for r in quali["classification"] if r["time"] is not None]
        untimed = [r for r in quali["classification"] if r["time"] is None]
        untimed.sort(key=lambda r: (rank.get(r["driver_id"], 10 ** 6), r["car"]))
        quali["classification"] = timed + untimed
        for pos, r in enumerate(quali["classification"], start=1):
            r["pos"] = pos
        grid = [int(r["car"]) for r in quali["classification"]]
        if publisher is not None:
            header = {"format": "raicing-replay/1", "kind": "race", "laps": laps,
                      "dt": regs.race.physics_dt * 20, "track": track.to_dict(decimate=2),
                      "cars": [{"car": i, "entry_id": e.entry_id, "team_id": e.team_id, "team": e.team_name,
                                "driver": e.driver_name, "driver_id": e.driver_id, "color": e.color,
                                "generation": e.generation, "kind": "brain" if e.brain is not None else "reference"}
                               for i, e in enumerate(entries)],
                      "frame_fields": ["x", "y", "psi", "speed", "status", "compound", "wear", "temp", "lap",
                                       "pos", "damage", "steer", "pedal"],
                      "status_names": STATUS_NAMES, "compounds": list(COMPOUNDS), "weather": weather.to_dict(),
                      "grid": [entries[i].entry_id for i in grid]}
            publisher.start(rnd.id, season.number, rnd.number, header)
        race = run_race(track, entries, grid, laps, regs, weather, seed=wseed, record=True, progress_cb=publisher)
        if publisher is not None:
            publisher.finish(race.get("replay"), race["classification"])
        # ---- persist
        self.s.execute(RaceSession.__table__.delete().where(RaceSession.round_id == rnd.id))
        self.s.execute(ResultRow.__table__.delete().where(ResultRow.round_id == rnd.id))
        self.s.execute(TeamPenalty.__table__.delete().where(TeamPenalty.round_id == rnd.id))
        race["round_info"] = {"corners": int(track.meta.get("corners", 0)), "track_seed": int(rnd.track_seed),
                              "weather": {"ambient": round(float(weather.ambient), 1),
                                          "rain": bool(max(weather.rain) > 0.0 or weather.initial_wetness > 0.0)},
                              "forfeits": forfeits}
        for kind, res in (("quali", quali), ("race", race)):
            replay = res.pop("replay", None)
            rel = None
            if replay is not None:
                rel = f"replays/{rnd.id}-{kind}.json.gz"
                save_json_gz(replay, self.data_dir / rel)
            self.s.add(RaceSession(round_id=rnd.id, kind=kind, result=to_jsonable(res), replay_path=rel))
        by_car = {i: c for i, c in enumerate(cars)}
        for r in race["classification"]:
            c = by_car[int(r["car"])]
            self.s.add(ResultRow(round_id=rnd.id, team_id=c.team.id, driver_id=c.driver.id, brain_id=c.brain_id,
                                 grid=grid.index(int(r["car"])) + 1, pos=int(r["pos"]), status=str(r["status"]),
                                 points=int(r["points"]), laps=int(r["laps"]),
                                 time=None if r["time"] is None else float(r["time"]),
                                 best_lap=None if r["best_lap"] is None else float(r["best_lap"]),
                                 pits=int(r["pits"]), penalty=float(r["penalty"])))
        dsq_by_team: dict[str, list[str]] = defaultdict(list)
        for ev in race.get("events", []):
            if ev.get("type") == "dsq" and isinstance(ev.get("car"), (int, np.integer)):
                c = by_car[int(ev["car"])]
                dsq_by_team[c.team.id].append(c.driver.name)
        for team_id, pts in race.get("team_penalties", {}).items():
            who = ", ".join(dsq_by_team.get(team_id, [])) or "un pilote"
            self.s.add(TeamPenalty(round_id=rnd.id, team_id=team_id, points=int(pts),
                                   reason=f"Élimination d'un adversaire par contact fautif ({who})"))
        # effective entries (explicit, default and system) are recorded for the round sheet
        for c in cars:
            row = self.s.scalar(select(EntryRecord).where(EntryRecord.round_id == rnd.id,
                                                          EntryRecord.driver_id == c.driver.id))
            if row is None:
                row = EntryRecord(round_id=rnd.id, team_id=c.team.id, driver_id=c.driver.id, submitted_at=None)
                self.s.add(row)
            row.brain_id = c.brain_id
            row.setup = asdict(c.setup)
        rnd.status = "done"
        rnd.ran_at = utcnow()
        rnd.laps = laps
        rnd.track_name = track.name
        rnd.track_length = float(track.length)
        self.s.commit()

    # ================================================================== standings
    def _standings(self, season: int, hidden: int | None, before_number: int | None = None) -> dict[str, Any]:
        rounds = self._rounds(season)
        counted = [r for r in rounds if r.status == "done" and r.id != hidden
                   and (before_number is None or r.number < before_number)]
        counted_ids = {r.id for r in counted}
        index = {r.id: k for k, r in enumerate(rounds)}
        ids = sorted(counted_ids)
        rows = list(self.s.scalars(select(ResultRow).where(ResultRow.round_id.in_(ids)))) if ids else []
        pens = list(self.s.scalars(select(TeamPenalty).where(TeamPenalty.round_id.in_(ids)))) if ids else []
        teams = {t.id: t for t in self._teams()}
        drivers = list(self.s.scalars(select(Driver).order_by(Driver.id)))

        def blank(rid_known: set[int]) -> list[int | None]:
            return [0 if r.id in rid_known else None for r in rounds]

        dstats: dict[str, dict[str, Any]] = {}
        for d in drivers:
            t = teams.get(d.team_id)
            if t is None:
                continue
            dstats[d.id] = {"driver_id": d.id, "driver": d.name, "team_id": t.id, "team": t.name, "color": t.color,
                            "points": 0, "wins": 0, "podiums": 0, "dnf": 0, "best": None,
                            "by_round": blank(counted_ids)}
        tstats: dict[str, dict[str, Any]] = {
            t.id: {"team_id": t.id, "team": t.name, "color": t.color, "points": 0, "penalties": 0, "wins": 0,
                   "podiums": 0, "best": None, "by_round": blank(counted_ids)} for t in teams.values()}
        for r in rows:
            ds = dstats.get(r.driver_id)
            ts = tstats.get(r.team_id)
            k = index[r.round_id]
            finished = r.status == "finished"
            for st in (ds, ts):
                if st is None:
                    continue
                st["points"] += r.points
                st["by_round"][k] = (st["by_round"][k] or 0) + r.points
                if finished and r.pos == 1:
                    st["wins"] += 1
                if finished and r.pos <= 3:
                    st["podiums"] += 1
                if finished:
                    st["best"] = r.pos if st["best"] is None else min(st["best"], r.pos)
            if ds is not None and not finished:
                ds["dnf"] += 1
        for p in pens:
            ts = tstats.get(p.team_id)
            if ts is None:
                continue
            k = index[p.round_id]
            ts["points"] -= p.points
            ts["penalties"] += p.points
            ts["by_round"][k] = (ts["by_round"][k] or 0) - p.points

        def key(st: dict[str, Any]) -> tuple:
            return (-st["points"], -st["wins"], -st["podiums"], st["best"] if st["best"] is not None else 10 ** 6,
                    st.get("driver_id", st.get("team_id")))

        dlist = sorted(dstats.values(), key=key)
        tlist = sorted(tstats.values(), key=key)
        for pos, st in enumerate(dlist, start=1):
            st["pos"] = pos
        for pos, st in enumerate(tlist, start=1):
            st["pos"] = pos
        dout = [{k: st[k] for k in ("pos", "driver_id", "driver", "team_id", "team", "color", "points", "wins",
                                    "podiums", "dnf", "by_round")} for st in dlist]
        tout = [{k: st[k] for k in ("pos", "team_id", "team", "color", "points", "penalties", "wins", "podiums",
                                    "by_round")} for st in tlist]
        return {"drivers": dout, "teams": tout}

    def get_standings(self, season: int) -> dict[str, Any]:
        self._season(season)
        out = self._standings(season, self._hidden_round())
        out["season"] = season
        return out

    # ================================================================== public reads
    def get_league(self) -> dict[str, Any]:
        lg = self._league()
        seasons = self._seasons()
        hidden = self._hidden_round()
        out = []
        for s in seasons:
            rounds = self._rounds(s.number)
            out.append({"number": s.number, "status": s.status, "rounds": len(rounds),
                        "rounds_done": sum(1 for r in rounds if self._visible_done(r, hidden))})
        cur = self._current_season()
        return {"name": lg.name, "current_season": cur.number if cur else None, "seasons": out,
                "created_at": iso(lg.created_at)}

    def _driver_brief(self, driver_id: str) -> dict[str, Any] | None:
        d = self.s.get(Driver, driver_id)
        if d is None:
            return None
        t = self.s.get(Team, d.team_id)
        return {"driver_id": d.id, "driver": d.name, "team_id": d.team_id, "team": t.name if t else "",
                "color": t.color if t else "#888888"}

    def _round_summary(self, rnd: Round, hidden: int | None) -> dict[str, Any]:
        done = self._visible_done(rnd, hidden)
        winner = pole = None
        if done:
            w = self.s.scalar(select(ResultRow).where(ResultRow.round_id == rnd.id, ResultRow.pos == 1))
            if w is not None:
                winner = self._driver_brief(w.driver_id)
            p = self.s.scalar(select(ResultRow).where(ResultRow.round_id == rnd.id, ResultRow.grid == 1))
            if p is not None:
                b = self._driver_brief(p.driver_id)
                pole = {"driver_id": b["driver_id"], "driver": b["driver"]} if b else None
        status = "running" if rnd.id == hidden else rnd.status
        return {"id": rnd.id, "season": rnd.season, "number": rnd.number, "status": status,
                "track_name": rnd.track_name if done else None,
                "track_length": round(rnd.track_length, 1) if done else round(rnd.track_length, -2),
                "laps": rnd.laps, "category": rnd.category, "rain_probability": rnd.rain_probability,
                "winner": winner, "pole": pole, "date": iso(rnd.ran_at if done else rnd.scheduled_at)}

    @staticmethod
    def _change_public(ch: dict[str, Any] | None, season: int) -> dict[str, Any] | None:
        if not ch:
            return None
        return {"season": season, "parameter": ch.get("path"), "label": ch.get("label"), "old": ch.get("old"),
                "new": ch.get("new"), "percent": ch.get("percent"), "affects": ch.get("affects"),
                "rationale": ch.get("rationale", ""), "bench": ch.get("bench", {})}

    def get_season(self, number: int) -> dict[str, Any]:
        s = self._season(number)
        hidden = self._hidden_round()
        return {"number": s.number, "status": s.status, "budget": s.budget, "quota": s.quota,
                "version": (s.regulations or {}).get("version"),
                "regulation_change": self._change_public(s.change, s.number),
                "started_at": iso(s.started_at), "finished_at": iso(s.finished_at),
                "rounds": [self._round_summary(r, hidden) for r in self._rounds(number)]}

    def get_round(self, round_id: int, embargo: bool = True) -> dict[str, Any]:
        rnd = self._round(round_id)
        hidden = self._hidden_round() if embargo else None
        done = self._visible_done(rnd, hidden)
        out: dict[str, Any] = {
            "id": rnd.id, "season": rnd.season, "number": rnd.number,
            "status": "running" if rnd.id == hidden else rnd.status, "laps": rnd.laps,
            "date": iso(rnd.ran_at if done else rnd.scheduled_at),
            "track": {"name": None, "length": round(rnd.track_length, -2), "seed": None, "corners": None,
                      "category": rnd.category},
            "rain_probability": rnd.rain_probability, "weather": None, "quali": None, "race": None,
            "entries": [], "forfeits": [], "has_replay": {"quali": False, "race": False},
        }
        if not done:
            out["entries"] = [{"entry_id": self._entry_id(self._driver(e.driver_id)), "driver_id": e.driver_id,
                               "team_id": e.team_id, "submitted_at": iso(e.submitted_at)}
                              for e in self.s.scalars(select(EntryRecord).where(EntryRecord.round_id == rnd.id)
                                                      .order_by(EntryRecord.driver_id))
                              if e.submitted_at is not None]
            return out
        sessions = {x.kind: x for x in self.s.scalars(select(RaceSession).where(RaceSession.round_id == rnd.id))}
        race = sessions.get("race")
        quali = sessions.get("quali")
        info = (race.result.get("round_info") if race else None) or {}
        out["track"] = {"name": rnd.track_name, "length": round(rnd.track_length, 1), "seed": rnd.track_seed,
                        "corners": info.get("corners"), "category": rnd.category}
        out["weather"] = info.get("weather")
        out["forfeits"] = info.get("forfeits", [])
        if quali is not None:
            out["quali"] = {"classification": quali.result.get("classification", [])}
        if race is not None:
            r = race.result
            out["race"] = {"classification": r.get("classification", []), "fastest_lap": r.get("fastest_lap"),
                           "incidents": r.get("incidents", []), "team_penalties": r.get("team_penalties", {}),
                           "grid": r.get("grid", [])}
        entries = []
        for e in self.s.scalars(select(EntryRecord).where(EntryRecord.round_id == rnd.id)
                                .order_by(EntryRecord.driver_id)):
            brain = None
            if e.brain_id is not None:
                b = self.s.get(BrainRecord, e.brain_id)
                if b is not None:
                    brain = {"id": b.id, "generation": b.generation, "method": b.method}
            team = self.s.get(Team, e.team_id)
            entries.append({"entry_id": self._entry_id(self._driver(e.driver_id)), "driver_id": e.driver_id,
                            "team_id": e.team_id, "brain": brain,
                            "reference": team.reference_personality if team is not None and team.is_system else None,
                            "setup": e.setup, "submitted": e.submitted_at is not None})
        out["entries"] = entries
        out["has_replay"] = {k: bool(sessions.get(k) and sessions[k].replay_path and
                                     (self.data_dir / sessions[k].replay_path).exists())
                             for k in ("quali", "race")}
        return out

    def get_replay_path(self, round_id: int, session: str = "race") -> Path:
        if session not in ("race", "quali"):
            raise LeagueError("session doit valoir race ou quali")
        rnd = self._round(round_id)
        if not self._visible_done(rnd, self._hidden_round()):
            raise NotFound("replay indisponible : manche pas encore courue (ou diffusion en cours)")
        sess = self.s.scalar(select(RaceSession).where(RaceSession.round_id == rnd.id, RaceSession.kind == session))
        if sess is None or not sess.replay_path or not (self.data_dir / sess.replay_path).exists():
            raise NotFound("replay introuvable")
        return self.data_dir / sess.replay_path

    def get_team(self, team_id: str) -> dict[str, Any]:
        t = self._team(team_id)
        season = self._current_season()
        drivers = []
        for d in self._drivers_of(t.id):
            ab = None
            if d.active_brain_id is not None:
                b = self.s.get(BrainRecord, d.active_brain_id)
                if b is not None:
                    ab = {"id": b.id, "generation": b.generation}
            drivers.append({"id": d.id, "name": d.name, "number": d.number, "active_brain": ab})
        garage = self.s.get(Garage, (t.id, season.number)) if season is not None else None
        if garage is None and season is not None:
            garage = self.s.scalar(select(Garage).where(Garage.team_id == t.id).order_by(Garage.season.desc())
                                   .limit(1))
        results = []
        if season is not None:
            hidden = self._hidden_round()
            for rnd in self._rounds(season.number):
                if not self._visible_done(rnd, hidden):
                    continue
                pts = self.s.scalar(select(func.coalesce(func.sum(ResultRow.points), 0))
                                    .where(ResultRow.round_id == rnd.id, ResultRow.team_id == t.id)) or 0
                pen = self.s.scalar(select(func.coalesce(func.sum(TeamPenalty.points), 0))
                                    .where(TeamPenalty.round_id == rnd.id, TeamPenalty.team_id == t.id)) or 0
                results.append({"round_id": rnd.id, "round": rnd.number, "season": rnd.season,
                                "points": int(pts) - int(pen), "penalty": int(pen)})
        quota = self._quota(t.id, season)
        return {"id": t.id, "name": t.name, "color": t.color, "director": t.director, "is_system": t.is_system,
                "reference": t.reference_personality if t.is_system else None, "drivers": drivers,
                "garage": self._garage_public(garage), "results": results,
                "quota": None if quota is None else {k: quota[k] for k in ("season", "used", "total")}}

    def list_teams(self) -> list[dict[str, Any]]:
        return [self.get_team(t.id) for t in self._teams()]

    def get_driver(self, driver_id: str) -> dict[str, Any]:
        d = self._driver(driver_id)
        t = self._team(d.team_id)
        active = None
        rating = None
        if d.active_brain_id is not None:
            b = self.s.get(BrainRecord, d.active_brain_id)
            if b is not None:
                active = {"id": b.id, "generation": b.generation, "method": b.method, "params": b.params,
                          "created_at": iso(b.created_at)}
                r = self._latest_rating(b.id)
                if r is not None:
                    rd = self._rating_dict(r)
                    rating = {"overall": rd["overall"], "ci": rd["ci"], "components": rd["components"]}
        gens = []
        for b in self.s.scalars(select(BrainRecord).where(BrainRecord.driver_id == d.id)
                                .order_by(BrainRecord.generation.desc())):
            r = self._latest_rating(b.id)
            gens.append({"id": b.id, "generation": b.generation, "method": b.method, "created_at": iso(b.created_at),
                         "rating": round(r.overall, 1) if r else None, "training_steps": b.training_steps,
                         "parent": b.parent_id})
        hidden = self._hidden_round()
        results = []
        for row, rnd in self.s.execute(select(ResultRow, Round).join(Round, Round.id == ResultRow.round_id)
                                       .where(ResultRow.driver_id == d.id)
                                       .order_by(Round.season, Round.number)).all():
            if rnd.id == hidden:
                continue
            results.append({"round_id": rnd.id, "round": rnd.number, "season": rnd.season, "pos": row.pos,
                            "status": row.status, "points": row.points, "grid": row.grid,
                            "brain_id": row.brain_id})
        return {"id": d.id, "name": d.name, "team_id": t.id, "team": t.name, "color": t.color, "number": d.number,
                "reference": t.reference_personality if t.is_system else None,
                "active_brain": active, "rating": rating, "generations": gens, "results": results}

    def get_regulations(self, season: int | None = None) -> dict[str, Any]:
        if season is None:
            cur = self._current_season()
            if cur is None:
                raise NotFound("aucune saison")
            season = cur.number
        s = self._season(season)
        changes = [self._change_public({"path": c.path, "label": c.label, "old": c.old, "new": c.new,
                                        "percent": c.percent, "affects": c.affects, "rationale": c.rationale,
                                        "bench": c.bench}, c.season)
                   for c in self.s.scalars(select(RegulationChangeRecord)
                                           .where(RegulationChangeRecord.season <= season)
                                           .order_by(RegulationChangeRecord.season))]
        return {"season": s.number, "version": (s.regulations or {}).get("version", f"S{s.number}"),
                "regulations": s.regulations, "changes": changes}

    def get_me(self, team_id: str) -> dict[str, Any]:
        out = self.get_team(team_id)
        season = self._current_season()
        out["quota"] = self._quota(team_id, season)
        nxt = None
        if season is not None and season.status == "active":
            rnd = self._next_round(season)
            if rnd is not None:
                nxt = self._round_summary(rnd, self._hidden_round())
                nxt["entries"] = {e.driver_id: {"brain_id": e.brain_id, "setup": e.setup,
                                                "submitted_at": iso(e.submitted_at)}
                                  for e in self.s.scalars(select(EntryRecord).where(
                                      EntryRecord.round_id == rnd.id, EntryRecord.team_id == team_id))}
        out["season"] = None if season is None else {"number": season.number, "status": season.status,
                                                      "version": (season.regulations or {}).get("version")}
        out["next_round"] = nxt
        out["jobs_active"] = int(self.s.scalar(select(func.count(Job.id)).where(
            Job.team_id == team_id, Job.status.in_(("queued", "running")))) or 0)
        return out

    def get_live(self) -> dict[str, Any]:
        return live_status(self.data_dir / "live")
