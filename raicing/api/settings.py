"""Settings shared by the API server and the worker (environment variables)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..league.service import ServiceOptions


def default_data_dir() -> Path:
    return Path(os.environ.get("RAICING_DATA_DIR") or os.environ.get("RAICING_LEAGUE_DIR") or "./league_data")


@dataclass
class ApiSettings:
    """``DATABASE_URL``, ``RAICING_DATA_DIR``, ``RAICING_ADMIN_TOKEN``, ``RAICING_CORS_ORIGINS`` + service options."""

    data_dir: Path = field(default_factory=default_data_dir)
    database_url: str | None = None          # None: SQLite file in data_dir
    admin_token: str | None = None           # None: admin endpoints disabled (403)
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    options: ServiceOptions = field(default_factory=ServiceOptions)
    live_poll: float = 0.25                  # seconds between reads of a growing live feed
    worker_interval: float = 5.0             # seconds between worker iterations when idle

    @classmethod
    def from_env(cls) -> "ApiSettings":
        origins = os.environ.get("RAICING_CORS_ORIGINS", "*")
        return cls(data_dir=default_data_dir(), database_url=os.environ.get("DATABASE_URL") or None,
                   admin_token=os.environ.get("RAICING_ADMIN_TOKEN") or None,
                   cors_origins=[o.strip() for o in origins.split(",") if o.strip()],
                   options=ServiceOptions.from_env(),
                   worker_interval=float(os.environ.get("RAICING_WORKER_INTERVAL", "5") or 5))
