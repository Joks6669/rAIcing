"""Shared CLI plumbing: configuration, output (human French text or ``--json``), error handling."""
from __future__ import annotations

import functools
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any

import typer

CONFIG_PATH = Path.home() / ".raicing" / "config.json"

JsonOpt = Annotated[bool, typer.Option("--json", help="Sortie JSON (pour les agents).")]


class CliError(Exception):
    """User-facing CLI error (French message)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


def load_config() -> dict[str, Any]:
    """``~/.raicing/config.json`` overridden by ``RAICING_*`` environment variables."""
    cfg: dict[str, Any] = {}
    path = Path(os.environ.get("RAICING_CONFIG", CONFIG_PATH))
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text()))
        except ValueError as exc:
            raise CliError(f"fichier de configuration illisible : {path} ({exc})") from exc
    env = {"mode": "RAICING_MODE", "league_dir": "RAICING_LEAGUE_DIR", "api_url": "RAICING_API_URL",
           "token": "RAICING_TOKEN", "team": "RAICING_TEAM", "admin_token": "RAICING_ADMIN_TOKEN"}
    for key, var in env.items():
        if os.environ.get(var):
            cfg[key] = os.environ[var]
    if "mode" not in cfg:
        cfg["mode"] = "remote" if cfg.get("api_url") and not cfg.get("league_dir") else "local"
    if cfg["mode"] not in ("local", "remote"):
        raise CliError("RAICING_MODE doit valoir local ou remote")
    return cfg


def emit(data: Any, json_out: bool, human: Callable[[Any], str] | str | None = None) -> None:
    """Print ``data`` as JSON (``--json``) or as human-readable French text."""
    if json_out:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return
    if human is None:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    elif isinstance(human, str):
        typer.echo(human)
    else:
        typer.echo(human(data))


def handle_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn league/CLI/HTTP errors into a clean message and exit code 1 (JSON when ``--json``)."""
    from ..league.service import LeagueError
    from ..league.training import TrainingUnavailable

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        json_out = bool(kwargs.get("json_out", False))
        try:
            return fn(*args, **kwargs)
        except (LeagueError, CliError, TrainingUnavailable) as exc:
            status = getattr(exc, "status", None)
            msg = getattr(exc, "message", str(exc))
        except OSError as exc:
            status, msg = None, f"{exc}"
        except Exception as exc:  # httpx transport errors, unexpected payloads
            if type(exc).__module__.startswith("httpx"):
                status, msg = None, f"API injoignable : {exc}"
            else:
                raise
        if json_out:
            typer.echo(json.dumps({"error": msg, "status": status}, ensure_ascii=False))
        else:
            typer.echo(f"Erreur : {msg}", err=True)
        raise typer.Exit(1)
    return wrapper


# ---------------------------------------------------------------------- formatting helpers
def fmt_time(t: float | None) -> str:
    if t is None:
        return "—"
    m, s = divmod(float(t), 60.0)
    return f"{int(m)}:{s:06.3f}" if m >= 1 else f"{s:.3f}"


def fmt_int(n: int | float | None) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", " ")


def table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> str:
    """Plain fixed-width table."""
    cells = [[("—" if v is None else str(v)) for v in r] for r in rows]
    widths = [len(h) for h in headers]
    for r in cells:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], len(v))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    out = [line, "  ".join("-" * w for w in widths)]
    out += ["  ".join(v.ljust(widths[i]) for i, v in enumerate(r)) for r in cells]
    return "\n".join(out)


def echo_err(msg: str) -> None:
    typer.echo(msg, err=True)


def stdout_is_tty() -> bool:
    return sys.stdout.isatty()
