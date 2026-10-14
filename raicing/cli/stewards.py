"""``stewards`` — the commissioners' CLI (league administration, docs/LEAGUE.md).

Mode ``local`` operates on ``RAICING_LEAGUE_DIR`` (or ``DATABASE_URL``); mode ``remote`` calls the
admin endpoints of the API with ``RAICING_ADMIN_TOKEN``. Every command accepts ``--json``.
"""
from __future__ import annotations

from typing import Annotated, Any, Optional

import typer

from .backend import make_backend
from .common import JsonOpt, emit, handle_errors, load_config, table

app = typer.Typer(name="stewards", help="CLI des commissaires — administration de l'AI Racing League.",
                  no_args_is_help=True, add_completion=False)
team_app = typer.Typer(help="Écuries.", no_args_is_help=True)
season_app = typer.Typer(help="Saisons.", no_args_is_help=True)
round_app = typer.Typer(help="Manches.", no_args_is_help=True)
app.add_typer(team_app, name="team")
app.add_typer(season_app, name="season")
app.add_typer(round_app, name="round")


def backend(create: bool = False):
    return make_backend(load_config(), admin=True, create=create)


def _fmt_round(r: dict[str, Any]) -> str:
    if r.get("race"):
        cls = r["race"]["classification"]
        podium = ", ".join(f"{c['pos']}. {c['driver']} ({c['team']})" for c in cls[:3])
        pens = r["race"].get("team_penalties") or {}
        extra = (" ; pénalités d'écurie : " + ", ".join(f"{k} −{v}" for k, v in pens.items())) if pens else ""
        return (f"Manche {r['number']} (id {r['id']}) courue : {r['track']['name']}, {r['laps']} tours. "
                f"Podium : {podium}{extra}")
    return f"Manche {r['number']} (id {r['id']}) : {r['status']}"


def _fmt_league(lg: dict[str, Any]) -> str:
    lines = [f"Ligue : {lg['name']} — saison courante : {lg.get('current_season')}"]
    lines += [f"  saison {s['number']} : {s['status']}, {s['rounds_done']}/{s['rounds']} manches courues"
              for s in lg.get("seasons", [])]
    return "\n".join(lines)


@app.command()
@handle_errors
def init(name: Annotated[str, typer.Option("--name", help="Nom de la ligue.")] = "AI Racing League",
         seed: Annotated[Optional[int], typer.Option("--seed", help="Graine (tests/ligues locales : rend le secret "
                                                                     "et donc les circuits prévisibles).")] = None,
         system_teams: Annotated[int, typer.Option("--system-teams", help="Écuries système à créer.")] = 6,
         rounds: Annotated[int, typer.Option("--rounds", help="Manches par saison.")] = 10,
         json_out: JsonOpt = False) -> None:
    """Crée la ligue (secret, saison 1 planifiée, manches) et les écuries système."""
    res = backend(create=True).init(name, seed, rounds, system_teams)
    emit(res, json_out, lambda r: _fmt_league(r["league"]) + "\nÉcuries système : "
         + (", ".join(r["system_teams"]) or "aucune"))


@app.command()
@handle_errors
def status(json_out: JsonOpt = False) -> None:
    """État de la ligue."""
    emit(backend().league(), json_out, _fmt_league)


@app.command("run-next")
@handle_errors
def run_next(json_out: JsonOpt = False) -> None:
    """Ouvre si besoin puis court la prochaine manche de la saison en cours."""
    emit(backend().run_next(), json_out, _fmt_round)


# ---------------------------------------------------------------------- teams
@team_app.command("create")
@handle_errors
def team_create(name: str,
                color: Annotated[str, typer.Option("--color", help="Couleur #RRGGBB.")] = "#888888",
                director: Annotated[str, typer.Option("--director", help="IA directrice (ex. « Astra 6 »).")] = "",
                drivers: Annotated[Optional[list[str]], typer.Option("--driver", help="Nom de pilote (×2).")] = None,
                json_out: JsonOpt = False) -> None:
    """Crée une écurie et affiche son jeton (une seule fois)."""
    res = backend().create_team(name, color, director, drivers)
    t = res["team"]
    emit(res, json_out, f"Écurie créée : {t['name']} ({t['id']}), pilotes "
                        + ", ".join(f"{d['id']} {d['name']} n°{d['number']}" for d in t["drivers"])
                        + f"\nJeton (affiché une seule fois, à transmettre au directeur) : {res['token']}")


@team_app.command("token")
@handle_errors
def team_token(team_id: str, json_out: JsonOpt = False) -> None:
    """Régénère le jeton d'une écurie (l'ancien est révoqué) et l'affiche une fois."""
    res = backend().rotate_token(team_id)
    emit(res, json_out, f"Nouveau jeton de {res['team_id']} (affiché une seule fois) : {res['token']}")


@team_app.command("list")
@handle_errors
def team_list(json_out: JsonOpt = False) -> None:
    """Écuries engagées."""
    data = backend().teams()
    emit(data, json_out, lambda ts: table(
        [[t["id"], t["name"], t.get("director") or "", t.get("reference") or "", t["color"],
          ", ".join(d["id"] for d in t["drivers"])] for t in ts],
        ["id", "écurie", "directeur", "pilote de référence", "couleur", "pilotes"]))


@team_app.command("fill")
@handle_errors
def team_fill(total: Annotated[int, typer.Option("--total", help="Nombre total d'écuries visé.")] = 8,
              json_out: JsonOpt = False) -> None:
    """Complète le plateau avec des écuries système."""
    res = backend().fill(total)
    emit(res, json_out, "Écuries système créées : " + (", ".join(res["created"]) or "aucune"))


# ---------------------------------------------------------------------- seasons
@season_app.command("start")
@handle_errors
def season_start(number: int, json_out: JsonOpt = False) -> None:
    """Démarre une saison (garages reconduits, budgets)."""
    res = backend().season_start(number)
    emit(res, json_out, f"Saison {res['number']} démarrée : budget {res['budget']}, {len(res['rounds'])} manches.")


@season_app.command("close")
@handle_errors
def season_close(number: int, json_out: JsonOpt = False) -> None:
    """Clôt une saison : changement réglementaire (banc historique) et saison suivante planifiée."""
    res = backend().season_close(number)
    ch = res["change"] or {}
    part = f", pièce concernée : {ch['affects']}" if ch.get("affects") else ""
    emit(res, json_out, f"Saison {res['season']} close. Saison {res['next_season']} planifiée.\n"
                        f"Changement : {ch.get('label')} ({ch.get('parameter')}) {ch.get('old')} → {ch.get('new')} "
                        f"({ch.get('percent')} %){part}")


@season_app.command("schedule")
@handle_errors
def season_schedule(number: int,
                    start: Annotated[str, typer.Option("--start", help="Date ISO 8601 de la prochaine manche (UTC).")],
                    every_hours: Annotated[float, typer.Option("--every-hours", help="Intervalle (h).")] = 168.0,
                    json_out: JsonOpt = False) -> None:
    """Programme les manches restantes (le worker les court à l'heure dite)."""
    res = backend().season_schedule(number, start, every_hours)
    emit(res, json_out, lambda s: "\n".join(f"Manche {r['number']} (id {r['id']}) : {r['date'] or '—'} [{r['status']}]"
                                            for r in s["rounds"]))


# ---------------------------------------------------------------------- rounds
@round_app.command("open")
@handle_errors
def round_open(round_id: int, json_out: JsonOpt = False) -> None:
    """Ouvre les inscriptions d'une manche."""
    res = backend().round_open(round_id)
    emit(res, json_out, f"Inscriptions ouvertes : manche {res['number']} (id {res['id']}).")


@round_app.command("run")
@handle_errors
def round_run(round_id: int,
              background: Annotated[bool, typer.Option("--background", help="Laisser le worker la courir.")] = False,
              json_out: JsonOpt = False) -> None:
    """Court qualifications + course d'une manche."""
    emit(backend().round_run(round_id, background), json_out, _fmt_round)


if __name__ == "__main__":
    app()
