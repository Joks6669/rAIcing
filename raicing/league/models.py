"""SQLAlchemy 2 models of the league (see docs/LEAGUE.md).

Portable between SQLite (local leagues, tests) and PostgreSQL (official server): only generic
column types are used and JSON documents go through ``sqlalchemy.JSON``.

JSON columns are *not* mutation-tracked: always assign a new object (``row.state = {...}``)
instead of mutating the stored dict in place.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Timezone-aware current UTC time (the only clock used by the league)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class League(Base):
    __tablename__ = "league"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    secret: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    color: Mapped[str] = mapped_column(String(16), default="#888888")
    director: Mapped[str] = mapped_column(String(200), default="")
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    reference_personality: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    number: Mapped[int] = mapped_column(Integer)
    # plain integer (no FK) to avoid a drivers <-> brains dependency cycle
    active_brain_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BrainRecord(Base):
    __tablename__ = "brains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    driver_id: Mapped[str] = mapped_column(ForeignKey("drivers.id"), index=True)
    generation: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(32), default="unknown")
    params: Mapped[int] = mapped_column(Integer, default=0)
    architecture: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("brains.id"), nullable=True)
    training_steps: Mapped[int] = mapped_column(BigInteger, default=0)
    path: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    imported: Mapped[bool] = mapped_column(Boolean, default=False)


class Season(Base):
    __tablename__ = "seasons"

    number: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    status: Mapped[str] = mapped_column(String(16), default="planned")   # planned | active | finished
    budget: Mapped[int] = mapped_column(Integer)
    quota: Mapped[int] = mapped_column(BigInteger)
    regulations: Mapped[dict[str, Any]] = mapped_column(JSON)
    change: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (UniqueConstraint("season", "number", name="uq_round_season_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[int] = mapped_column(ForeignKey("seasons.number"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="scheduled")  # scheduled|entries_open|running|done
    track_seed: Mapped[int] = mapped_column(BigInteger)
    weather_seed: Mapped[int] = mapped_column(BigInteger)
    laps: Mapped[int] = mapped_column(Integer)
    track_name: Mapped[str] = mapped_column(String(200), default="")
    track_length: Mapped[float] = mapped_column(Float)
    category: Mapped[str] = mapped_column(String(16))
    rain_probability: Mapped[float] = mapped_column(Float)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ran_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EntryRecord(Base):
    __tablename__ = "entries"
    __table_args__ = (UniqueConstraint("round_id", "driver_id", name="uq_entry_round_driver"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    driver_id: Mapped[str] = mapped_column(ForeignKey("drivers.id"))
    brain_id: Mapped[int | None] = mapped_column(ForeignKey("brains.id"), nullable=True)
    setup: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # None = default entry generated by race control (no explicit submission)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RaceSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8))     # quali | race
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    replay_path: Mapped[str | None] = mapped_column(String(300), nullable=True)


class ResultRow(Base):
    __tablename__ = "result_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    driver_id: Mapped[str] = mapped_column(ForeignKey("drivers.id"), index=True)
    brain_id: Mapped[int | None] = mapped_column(ForeignKey("brains.id"), nullable=True)
    grid: Mapped[int] = mapped_column(Integer)
    pos: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    points: Mapped[int] = mapped_column(Integer, default=0)
    laps: Mapped[int] = mapped_column(Integer, default=0)
    time: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_lap: Mapped[float | None] = mapped_column(Float, nullable=True)
    pits: Mapped[int] = mapped_column(Integer, default=0)
    penalty: Mapped[float] = mapped_column(Float, default=0.0)


class TeamPenalty(Base):
    __tablename__ = "team_penalties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    points: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="")


class Garage(Base):
    __tablename__ = "garages"

    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    season: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)


class GarageTx(Base):
    __tablename__ = "garage_tx"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    season: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))     # construction | research | revision
    item: Mapped[str] = mapped_column(String(64))
    cost: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    driver_id: Mapped[str | None] = mapped_column(ForeignKey("drivers.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed|cancelled
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    steps_requested: Mapped[int] = mapped_column(BigInteger, default=0)
    steps_used: Mapped[int] = mapped_column(BigInteger, default=0)
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_brain_id: Mapped[int | None] = mapped_column(ForeignKey("brains.id"), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuotaLedger(Base):
    __tablename__ = "quota_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    steps: Mapped[int] = mapped_column(BigInteger)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RatingRecord(Base):
    __tablename__ = "ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brain_id: Mapped[int] = mapped_column(ForeignKey("brains.id"), index=True)
    season: Mapped[int] = mapped_column(Integer)
    overall: Mapped[float] = mapped_column(Float)
    ci_low: Mapped[float] = mapped_column(Float)
    ci_high: Mapped[float] = mapped_column(Float)
    components: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RegulationChangeRecord(Base):
    __tablename__ = "regulation_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[int] = mapped_column(Integer, index=True)   # season in which the change applies
    path: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(200))
    old: Mapped[float] = mapped_column(Float)
    new: Mapped[float] = mapped_column(Float)
    percent: Mapped[float] = mapped_column(Float)
    affects: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    bench: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
