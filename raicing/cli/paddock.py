"""``paddock`` — the team directors' CLI (docs/LEAGUE.md, docs/DIRECTOR_GUIDE.md).

Mode ``local`` (``RAICING_LEAGUE_DIR``) calls the league services directly; mode ``remote``
(``RAICING_API_URL`` + ``RAICING_TOKEN``) calls the HTTP API. Every command accepts ``--json``.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from ..brains.spec import Brain
from .backend import make_backend
from .common import CliError, JsonOpt, emit, fmt_int, fmt_time, handle_errors, load_config, table

app = typer.Typer(name="paddock", help="CLI des directeurs d'écurie — AI Racing League.",
                  no_args_is_help=True, add_completion=False)
garage_app = typer.Typer(help="Garage : catalogue, construction, recherche, révisions.", no_args_is_help=True)
brains_app = typer.Typer(help="Cerveaux (générations de pilotes).", no_args_is_help=True)
lab_app = typer.Typer(help="Laboratoire d'entraînement.", no_args_is_help=True)
entry_app = typer.Typer(help="Inscriptions aux manches.", no_args_is_help=True)
app.add_typer(garage_app, name="garage")
app.add_typer(brains_app, name="brains")
app.add_typer(lab_app, name="lab")
app.add_typer(entry_app, name="entry")


def backend():
    return make_backend(load_config())


# ====================================================================== formatting
def _pct(x: float | None) -> str:
    return "—" if x is None else f"{100 * float(x):.0f} %"


def _fmt_garage(g: dict[str, Any] | None) -> list[str]:
    if not g:
        return ["Garage : —"]
    cap = g.get("capacity", {})
    vol, th = cap.get("volume", [0, 0]), cap.get("thermal", [0, 0])
    lines = [f"Garage (saison {g['season']}) : budget {g['budget']}, dépensé {g['spent']}, disponible {g['available']}",
             "  pièces : " + ", ".join(f"{k} {v}" for k, v in g.get("parts", {}).items()),
             "  technologies : " + (", ".join(g.get("technologies", [])) or "aucune"),
             f"  capacité : encombrement {vol[0]}/{vol[1]}, thermique {th[0]}/{th[1]}, "
             f"masse {cap.get('mass_delta', 0):+} kg"]
    if g.get("non_compliant"):
        lines.append("  NON CONFORMES (révision requise) : "
                     + ", ".join(f"{k} plafonné au niveau {v}" for k, v in g["non_compliant"].items()))
    return lines


def _fmt_round_line(r: dict[str, Any]) -> str:
    name = r.get("track_name") or "circuit secret"
    date = r.get("date") or "date non fixée"
    win = r.get("winner")
    w = f" — vainqueur {win['driver']} ({win['team']})" if win else ""
    return (f"Manche {r['number']} (id {r['id']}) [{r['status']}] {name}, ~{r['track_length']:.0f} m, "
            f"{r['laps']} tours, {r['category']}, pluie {_pct(r.get('rain_probability'))}, {date}{w}")


def _fmt_status(me: dict[str, Any]) -> str:
    lines = [f"Écurie : {me['name']} ({me['id']}) — directeur : {me.get('director') or '—'}"]
    s = me.get("season")
    if s:
        lines.append(f"Saison {s['number']} ({s['status']}, règlement {s.get('version')})")
    lines.append("Pilotes :")
    for d in me.get("drivers", []):
        ab = d.get("active_brain")
        brain = f"cerveau actif #{ab['id']} (gén. {ab['generation']})" if ab else "AUCUN cerveau actif (forfait)"
        lines.append(f"  {d['id']}  {d['name']}  n°{d['number']}  {brain}")
    lines += _fmt_garage(me.get("garage"))
    q = me.get("quota")
    if q:
        lines.append(f"Quota d'entraînement : {fmt_int(q['used'])} / {fmt_int(q['total'])} "
                     f"(reste {fmt_int(q.get('remaining'))})")
    nxt = me.get("next_round")
    if nxt:
        lines.append("Prochaine : " + _fmt_round_line(nxt))
        ents = nxt.get("entries") or {}
        for d in me.get("drivers", []):
            e = ents.get(d["id"])
            if e and e.get("submitted_at"):
                lines.append(f"  inscrit : {d['id']} → cerveau {e['brain_id']} {e['setup']}")
            else:
                lines.append(f"  {d['id']} : inscription par défaut (cerveau actif, dernier réglage)")
    else:
        lines.append("Prochaine manche : aucune (saison non démarrée ou terminée)")
    lines.append(f"Entraînements en file ou en cours : {me.get('jobs_active', 0)}")
    return "\n".join(lines)


KEY_PARAMS = [
    ("car.power", "Puissance (W)"), ("car.cla", "Appui ClA"), ("car.cda", "Traînée CdA"), ("car.mass", "Masse (kg)"),
    ("car.mu_peak", "Adhérence pneus"), ("car.max_brake_force", "Freinage max (N)"),
    ("car.tyre_cool_speed", "Refroidissement pneus"), ("compounds.soft.wear_rate", "Usure tendre"),
    ("compounds.medium.wear_rate", "Usure medium"), ("compounds.hard.wear_rate", "Usure dur"),
    ("compounds.soft.grip", "Adhérence tendre"), ("race.pit_lane_time", "Passage aux stands (s)"),
    ("race.pit_service_time", "Service aux stands (s)"), ("aero.slipstream_drag_cut", "Aspiration"),
    ("aero.dirty_air_downforce_cut", "Air perturbé"), ("weather.rain_probability", "Probabilité de pluie"),
    ("training.season_quota", "Quota d'interactions"), ("training.max_concurrent_jobs", "Entraînements simultanés"),
    ("training.allow_external_brains", "Cerveaux externes autorisés"),
]


def _get(d: dict[str, Any], path: str) -> Any:
    for p in path.split("."):
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return d


def _fmt_regs(data: dict[str, Any]) -> str:
    regs = data.get("regulations", {})
    lines = [f"Règlement de la saison {data['season']} (version {data.get('version')})"]
    for path, label in KEY_PARAMS:
        lines.append(f"  {label:<30} {path:<34} {_get(regs, path)}")
    lines.append(f"  Barème : {_get(regs, 'race.points')}")
    changes = data.get("changes", [])
    if changes:
        lines.append("Changements réglementaires :")
        for c in changes:
            aff = f" — pièce concernée : {c['affects']} (révision nécessaire)" if c.get("affects") else ""
            lines.append(f"  saison {c['season']} : {c['label']} ({c['parameter']}) {c['old']} → {c['new']} "
                         f"({c['percent']:+.1f} %){aff}")
    else:
        lines.append("Aucun changement réglementaire.")
    return "\n".join(lines)


def _fmt_catalog(cat: dict[str, Any]) -> str:
    rows = []
    for p in cat["parts"]:
        blocked = "; ".join(p["blocked_by"]) if p["blocked_by"] else ""
        if p.get("non_compliant_cap") is not None:
            blocked = f"NON CONFORME (plafond {p['non_compliant_cap']}, révision {p['revision_cost']})"
        rows.append([p["part"], p["label"], p["level"], p["next_cost"], f"{p['current_gain']:.3f}→{p['next_gain']:.3f}",
                     f"{p['volume_per_level']}/{p['thermal_per_level']}/{p['mass_per_level']}", blocked])
    techs = [[t["technology"], t["label"], t["cost"], "oui" if t["owned"] else "", t["description"]]
             for t in cat["technologies"]]
    cap = cat["capacity"]
    return "\n".join([
        f"Saison {cat.get('season')} — budget {cat['budget']}, dépensé {cat['spent']}, disponible {cat['available']}",
        f"Capacité : encombrement {cap['volume'][0]}/{cap['volume'][1]}, thermique {cap['thermal'][0]}/"
        f"{cap['thermal'][1]}, masse {cap['mass_delta']:+} kg", "",
        table(rows, ["pièce", "libellé", "niv.", "coût suivant", "gain", "vol/therm/masse", "bloqué par"]), "",
        table(techs, ["technologie", "libellé", "coût", "acquise", "effet"]), "",
        "Exigences : " + "; ".join(f"{r['part']} ≥ {r['from_level']} exige {r['requires']}"
                                   for r in cat.get("requirements", []))])


def _fmt_garage_action(res: dict[str, Any]) -> str:
    g = res["garage"]
    return f"OK : {res['kind']} {res['item']} pour {res['cost']} points. Disponible : {g['available']}."


def _fmt_brains(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "Aucun cerveau. Entraînez-en un avec `paddock lab train CONFIG.json`."
    return table([[b["id"], b["driver_id"], b["generation"], b["method"], fmt_int(b["params"]),
                   fmt_int(b["training_steps"]), b.get("parent"), b.get("rating"),
                   ",".join(b.get("active_for", [])), b["created_at"][:16]] for b in rows],
                 ["id", "pilote", "gén.", "méthode", "params", "interactions", "parent", "rating", "actif", "créé"])


def _fmt_job(j: dict[str, Any]) -> str:
    p = j.get("progress") or {}
    extra = ""
    if "steps" in p:
        extra = f", {fmt_int(p['steps'])} / {fmt_int(j['steps_requested'])} interactions"
    if p.get("mean_reward") is not None:
        extra += f", récompense moyenne {float(p['mean_reward']):.3f}"
    s = f"Job {j['id']} ({j['kind']}{' ' + j['method'] if j.get('method') else ''}, pilote {j.get('driver_id')}) : " \
        f"{j['status']}{extra}"
    if j.get("result_brain_id"):
        s += f" → cerveau {j['result_brain_id']}"
    if j.get("error"):
        s += f"\n  erreur : {j['error']}"
    if p.get("rating"):
        s += f"\n  rating : {p['rating'].get('overall')} {p['rating'].get('ci')}"
    return s


def _fmt_results(r: dict[str, Any]) -> str:
    tr = r["track"]
    head = (f"Saison {r['season']}, manche {r['number']} (id {r['id']}) [{r['status']}] — "
            f"{tr.get('name') or 'circuit secret'}, {tr['length']} m, {r['laps']} tours, {tr['category']}")
    if not r.get("race"):
        return head + "\nPas encore de résultats."
    w = r.get("weather") or {}
    rain = "oui" if w.get("rain") else "non"
    lines = [head, f"Météo : {w.get('ambient')} °C, pluie : {rain}", "", "Qualifications :"]
    lines.append(table([[q["pos"], q["driver"], q["team"], fmt_time(q["time"]),
                         "" if q.get("gap") in (None, 0, 0.0) else f"+{q['gap']:.3f}", q["compound"],
                         q.get("reason") or ""] for q in r["quali"]["classification"]],
                       ["pos", "pilote", "écurie", "temps", "écart", "gomme", "raison"]))
    lines += ["", "Course :"]
    lines.append(table([[c["pos"], c["driver"], c["team"],
                         c["status"] + (f" ({c['reason']})" if c.get("reason") else ""), c["laps"],
                         fmt_time(c["time"]) if c["pos"] == 1 else (c.get("gap") or fmt_time(c["time"])),
                         fmt_time(c["best_lap"]), c["pits"], c["points"],
                         f"+{c['penalty']:.0f}s" if c.get("penalty") else ""] for c in r["race"]["classification"]],
                       ["pos", "pilote", "écurie", "statut", "tours", "temps/écart", "meilleur tour", "arrêts", "pts",
                        "pénalité"]))
    fl = r["race"].get("fastest_lap")
    if fl:
        lines.append(f"Meilleur tour : {fl['driver']} {fmt_time(fl['time'])}")
    tp = r["race"].get("team_penalties") or {}
    if tp:
        lines.append("Pénalités d'écurie : " + ", ".join(f"{k} −{v} pts" for k, v in tp.items()))
    for inc in r["race"].get("incidents", []):
        lines.append(f"  incident t={inc['t']} {' / '.join(inc['cars'])} ({inc['kind']}), fautif : "
                     f"{inc.get('fault') or 'aucun'}{' — pénalisé' if inc.get('penalised') else ''}")
    for f in r.get("forfeits", []):
        lines.append(f"  forfait : {f['driver_id']} ({f['reason']})")
    return "\n".join(lines)


def _fmt_standings(st: dict[str, Any]) -> str:
    d = table([[x["pos"], x["driver"], x["driver_id"], x["team"], x["points"], x["wins"], x["podiums"], x["dnf"],
                " ".join("·" if v is None else str(v) for v in x["by_round"])] for x in st["drivers"]],
              ["pos", "pilote", "id", "écurie", "pts", "vict.", "podiums", "abandons", "par manche"])
    t = table([[x["pos"], x["team"], x["team_id"], x["points"], x["penalties"],
                " ".join("·" if v is None else str(v) for v in x["by_round"])] for x in st["teams"]],
              ["pos", "écurie", "id", "pts", "pénalités", "par manche"])
    return f"Classement pilotes (saison {st.get('season')}) :\n{d}\n\nClassement écuries :\n{t}"


# ====================================================================== helpers
def _current_season(be) -> int:
    cur = be.league().get("current_season")
    if cur is None:
        raise CliError("aucune saison")
    return int(cur)


def _resolve_round(be, value: str | None) -> int:
    """``None``/``last`` -> last raced round; ``next`` -> next round; else a round id."""
    if value not in (None, "last", "next"):
        try:
            return int(value)
        except ValueError as exc:
            raise CliError(f"identifiant de manche invalide : {value}") from exc
    season = be.season(_current_season(be))
    rounds = season["rounds"]
    if value == "next":
        nxt = [r for r in rounds if r["status"] in ("scheduled", "entries_open")]
        if not nxt:
            raise CliError("aucune manche à venir dans la saison en cours")
        return int(nxt[0]["id"])
    done = [r for r in rounds if r["status"] == "done"]
    if not done:
        raise CliError("aucune manche courue dans la saison en cours")
    return int(done[-1]["id"])


# ====================================================================== commands
@app.command()
@handle_errors
def status(json_out: JsonOpt = False) -> None:
    """Écurie, pilotes, budget, quota, prochaine manche."""
    emit(backend().me(), json_out, _fmt_status)


@app.command()
@handle_errors
def regs(season: Annotated[Optional[int], typer.Option("--season", help="Saison (défaut : en cours).")] = None,
         json_out: JsonOpt = False) -> None:
    """Règlement en vigueur et changements de l'intersaison."""
    emit(backend().regulations(season), json_out, _fmt_regs)


@app.command()
@handle_errors
def calendar(season: Annotated[Optional[int], typer.Option("--season")] = None, json_out: JsonOpt = False) -> None:
    """Calendrier de la saison (circuits révélés après la course)."""
    be = backend()
    data = be.season(season or _current_season(be))
    emit(data, json_out, lambda s: "\n".join(
        [f"Saison {s['number']} ({s['status']}) — budget {s['budget']}, quota {fmt_int(s['quota'])}"]
        + [_fmt_round_line(r) for r in s["rounds"]]))


@app.command()
@handle_errors
def results(round_id: Annotated[Optional[str], typer.Argument(metavar="[ROUND]",
                                                              help="Id de manche (défaut : dernière courue).")] = None,
            json_out: JsonOpt = False) -> None:
    """Résultats d'une manche (qualifications, course, incidents)."""
    be = backend()
    emit(be.round(_resolve_round(be, round_id)), json_out, _fmt_results)


@app.command()
@handle_errors
def standings(season: Annotated[Optional[int], typer.Option("--season")] = None, json_out: JsonOpt = False) -> None:
    """Classements pilotes et écuries."""
    be = backend()
    n = season or _current_season(be)
    data = be.standings(n)
    data.setdefault("season", n)
    emit(data, json_out, _fmt_standings)


@app.command()
@handle_errors
def replay(round_id: Annotated[str, typer.Argument(metavar="ROUND")],
           out: Annotated[Optional[Path], typer.Option("--out", help="Fichier (.json ou .json.gz).")] = None,
           session: Annotated[str, typer.Option("--session", help="race ou quali.")] = "race",
           json_out: JsonOpt = False) -> None:
    """Télécharge le replay d'une manche courue."""
    be = backend()
    rid = _resolve_round(be, round_id)
    data = be.replay(rid, session)
    path = out or Path(f"replay-{rid}-{session}.json.gz")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        with gzip.open(path, "wb") as f:
            f.write(data)
    else:
        path.write_bytes(data)
    info = {"round_id": rid, "session": session, "path": str(path), "bytes": path.stat().st_size}
    emit(info, json_out, f"Replay écrit dans {path} ({fmt_int(info['bytes'])} octets).")


@app.command()
@handle_errors
def teams(json_out: JsonOpt = False) -> None:
    """Écuries engagées (garages publics)."""
    data = backend().teams()
    emit(data, json_out, lambda ts: table(
        [[t["id"], t["name"], t.get("director") or "", "système" if t["is_system"] else "",
          " ".join(f"{k[:3]}{v}" for k, v in ((t.get("garage") or {}).get("parts") or {}).items()),
          ", ".join(d["name"] for d in t["drivers"])] for t in ts],
        ["id", "écurie", "directeur", "type", "pièces", "pilotes"]))


@app.command()
@handle_errors
def driver(driver_id: str, json_out: JsonOpt = False) -> None:
    """Fiche d'un pilote (générations, rating, résultats)."""
    data = backend().driver(driver_id)

    def fmt(d: dict[str, Any]) -> str:
        ab = d.get("active_brain")
        pilot = f"#{ab['id']} gén. {ab['generation']} {ab['method']}" if ab else (d.get("reference") or "—")
        lines = [f"{d['name']} ({d['id']}) n°{d['number']} — {d['team']}", f"Cerveau actif : {pilot}"]
        if d.get("rating"):
            lines.append(f"Rating : {d['rating']['overall']} {d['rating']['ci']} {d['rating']['components']}")
        lines.append("Résultats : " + (", ".join(f"S{r['season']}M{r['round']} P{r['pos']} ({r['points']} pts)"
                                                for r in d["results"]) or "aucun"))
        return "\n".join(lines)
    emit(data, json_out, fmt)


# ---------------------------------------------------------------------- garage
@garage_app.command("catalog")
@handle_errors
def garage_catalog(json_out: JsonOpt = False) -> None:
    """Pièces, coût du niveau suivant, technologies, révisions nécessaires."""
    emit(backend().catalog(), json_out, _fmt_catalog)


@garage_app.command("upgrade")
@handle_errors
def garage_upgrade(part: str, json_out: JsonOpt = False) -> None:
    """Monte une pièce d'un niveau (engine, aero, bodywork, chassis, brakes, cooling, suspension)."""
    emit(backend().upgrade(part), json_out, _fmt_garage_action)


@garage_app.command("research")
@handle_errors
def garage_research(technology: str, json_out: JsonOpt = False) -> None:
    """Acquiert une technologie."""
    emit(backend().research(technology), json_out, _fmt_garage_action)


@garage_app.command("revise")
@handle_errors
def garage_revise(part: str, json_out: JsonOpt = False) -> None:
    """Révise une pièce rendue non conforme par le changement de règlement."""
    emit(backend().revise(part), json_out, _fmt_garage_action)


# ---------------------------------------------------------------------- brains
@brains_app.command("list")
@handle_errors
def brains_list(json_out: JsonOpt = False) -> None:
    """Générations de l'écurie."""
    emit(backend().brains(), json_out, _fmt_brains)


@brains_app.command("show")
@handle_errors
def brains_show(brain_id: int, json_out: JsonOpt = False) -> None:
    """Détails d'un cerveau (architecture, méta, ratings)."""
    emit(backend().brain(brain_id), json_out)


@brains_app.command("import")
@handle_errors
def brains_import(directory: Annotated[Path, typer.Argument(metavar="DIR", help="Dossier brain.json + weights.npz.")],
                  driver_id: Annotated[str, typer.Option("--driver", help="Pilote destinataire.")],
                  method: Annotated[str, typer.Option("--method")] = "external",
                  json_out: JsonOpt = False) -> None:
    """Importe un paquet cerveau (seulement si le règlement autorise les cerveaux externes)."""
    if not (directory / "brain.json").exists():
        raise CliError(f"{directory}/brain.json introuvable")
    brain = Brain.load(directory)
    res = backend().import_brain(driver_id, brain, method)
    emit(res, json_out, f"Cerveau importé : id {res['id']}, génération {res['generation']} de {res['driver_id']}.")


@brains_app.command("export")
@handle_errors
def brains_export(brain_id: int, directory: Annotated[Path, typer.Argument(metavar="DIR")],
                  json_out: JsonOpt = False) -> None:
    """Exporte un de vos cerveaux (brain.json + weights.npz) pour l'évaluer hors ligue."""
    brain = Brain.from_bytes(backend().export_brain(brain_id))
    brain.save(directory)
    emit({"brain_id": brain_id, "path": str(directory)}, json_out, f"Cerveau {brain_id} exporté dans {directory}.")


@brains_app.command("activate")
@handle_errors
def brains_activate(driver_id: Annotated[str, typer.Argument(metavar="DRIVER")],
                    brain_id: Annotated[int, typer.Argument(metavar="ID")], json_out: JsonOpt = False) -> None:
    """Définit le cerveau actif d'un pilote (utilisé par défaut en course)."""
    res = backend().activate(driver_id, brain_id)
    emit(res, json_out, f"{res['driver_id']} pilote désormais avec le cerveau {res['active_brain']['id']} "
                        f"(génération {res['active_brain']['generation']}).")


@brains_app.command("rate")
@handle_errors
def brains_rate(brain_id: int, quick: Annotated[bool, typer.Option("--quick", help="Batterie courte.")] = False,
                wait: Annotated[bool, typer.Option("--wait/--no-wait", help="Attendre le résultat (serveur).")] = True,
                json_out: JsonOpt = False) -> None:
    """Driver Rating d'un cerveau (évaluation sur circuits de validation, sans effet en course)."""
    be = backend()

    def progress(job: dict[str, Any]) -> None:
        if not json_out:
            typer.echo(f"… rating en cours (job {job['id']}, {job['status']})", err=True)
    res = be.rate(brain_id, quick or None, wait, progress)

    def fmt(r: dict[str, Any]) -> str:
        if "rating" not in r:
            return _fmt_job(r)
        rt = r["rating"]
        return (f"Driver Rating du cerveau {brain_id} : {rt['overall']} (IC {rt['ci'][0]}–{rt['ci'][1]})\n  "
                + ", ".join(f"{k} {v}" for k, v in rt["components"].items()))
    emit(res, json_out, fmt)


# ---------------------------------------------------------------------- lab
@lab_app.command("train")
@handle_errors
def lab_train(config_path: Annotated[Path, typer.Argument(metavar="CONFIG.json")],
              wait: Annotated[bool, typer.Option("--wait", help="Attendre la fin (mode serveur).")] = False,
              json_out: JsonOpt = False) -> None:
    """Lance un entraînement (en local : exécuté immédiatement dans ce processus)."""
    try:
        config = json.loads(config_path.read_text())
    except (OSError, ValueError) as exc:
        raise CliError(f"configuration illisible : {exc}") from exc
    be = backend()

    def progress(job: dict[str, Any]) -> None:
        if not json_out:
            typer.echo("… " + _fmt_job(job), err=True)
    if be.mode == "local" and not json_out:
        typer.echo("Entraînement local en cours (le quota est décompté)…", err=True)
    emit(be.train(config, wait, progress), json_out, _fmt_job)


@lab_app.command("jobs")
@handle_errors
def lab_jobs(json_out: JsonOpt = False) -> None:
    """Entraînements et ratings de l'écurie."""
    emit(backend().jobs(), json_out, lambda js: "\n".join(_fmt_job(j) for j in js) or "Aucun entraînement.")


@lab_app.command("job")
@handle_errors
def lab_job(job_id: int, json_out: JsonOpt = False) -> None:
    """Suivi d'un entraînement."""
    emit(backend().job(job_id), json_out, _fmt_job)


@lab_app.command("cancel")
@handle_errors
def lab_cancel(job_id: int, json_out: JsonOpt = False) -> None:
    """Annule un entraînement (le quota déjà consommé reste décompté)."""
    emit(backend().cancel(job_id), json_out, _fmt_job)


ARCH = {"ppo": [256, 256], "sac": [256, 256], "td3": [256, 256], "es": [64, 64], "bc": [256, 256]}
STEPS = {"ppo": 2_000_000, "sac": 1_000_000, "td3": 1_000_000, "es": 4_000_000, "bc": 500_000}
# a few common hyperparameters per method (names of raicing.lab.algos; the lab fills the others)
HYPER: dict[str, dict[str, Any]] = {
    "ppo": {"lr": 3e-4, "n_steps": 256, "batch_size": 4096, "n_epochs": 5, "gamma": 0.99, "gae_lambda": 0.95,
            "clip_range": 0.2, "ent_coef": 0.0},
    "sac": {"lr": 3e-4, "buffer_size": 300_000, "batch_size": 512, "gamma": 0.99, "tau": 0.005,
            "learning_starts": 10_000, "gradient_steps": 4},
    "td3": {"lr": 3e-4, "buffer_size": 300_000, "batch_size": 512, "gamma": 0.99, "tau": 0.005,
            "learning_starts": 10_000, "policy_delay": 2, "action_noise": 0.1},
    "es": {"population": 64, "sigma": 0.02, "lr": 0.01, "weight_decay": 0.005},
    "bc": {"teacher": "pro", "dagger": True, "iterations": 8, "epochs": 4, "lr": 1e-3, "batch_size": 2048},
}


def lab_template_config(method: str, driver_id: str) -> dict[str, Any]:
    """Example configuration following docs/LAB.md (built on the lab's defaults when it is installed)."""
    cfg: dict[str, Any] = {
        "driver_id": driver_id, "method": method, "steps": STEPS[method], "init_from": None,
        "architecture": {"hidden": ARCH[method], "activation": "tanh", "gru": 0, "layernorm": False,
                         "outputs": 11, "squash": None},
        "hyperparams": {},
        "reward": {"progress": 1.0, "time": -0.02, "crash": -20.0, "wear": -1.0, "smooth": 0.0, "edge": 0.0,
                   "finish": 10.0, "position": 0.0, "contact": -5.0},
        "curriculum": {"mode": "solo", "laps": 3, "opponents": ["pro", "prudent"], "rain_fraction": 0.2,
                       "tyre_randomization": True, "track_rotation": 4000},
        "envs": 64, "seed": 0,
    }
    try:
        import copy

        from ..league.training import lab_module
        base = copy.deepcopy(lab_module().DEFAULT_CONFIG)
        base.update({k: cfg[k] for k in ("driver_id", "method", "steps", "init_from")})
        base["architecture"] = {**base["architecture"], **cfg["architecture"]}
        cfg = base
    except Exception:   # lab not installed: documented fallback
        pass
    cfg["hyperparams"] = dict(HYPER[method])
    cfg["activate"] = False      # league option: activate the new generation when training ends
    return cfg


@lab_app.command("template")
@handle_errors
def lab_template(method: Annotated[str, typer.Option("--method", help="ppo | sac | td3 | es | bc")] = "ppo",
                 driver_id: Annotated[Optional[str], typer.Option("--driver", help="Pilote à entraîner.")] = None,
                 out: Annotated[Optional[Path], typer.Option("--out", help="Fichier (défaut : lab-<méthode>.json, "
                                                                     "'-' = sortie standard).")] = None,
                 json_out: JsonOpt = False) -> None:
    """Écrit un exemple de configuration d'entraînement (docs/LAB.md)."""
    if method not in STEPS:
        raise CliError("méthode inconnue : ppo, sac, td3, es ou bc")
    if driver_id is None:
        try:
            driver_id = backend().me()["drivers"][0]["id"]
        except Exception:   # not configured yet: placeholder
            driver_id = "VOTRE-ECURIE-1"
    cfg = lab_template_config(method, driver_id)
    if out is not None and str(out) == "-":
        typer.echo(json.dumps(cfg, ensure_ascii=False, indent=2))
        return
    path = out or Path(f"lab-{method}.json")
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    emit({"path": str(path), "config": cfg}, json_out,
         f"Configuration {method} écrite dans {path} (pilote {driver_id}). Ajustez-la puis : "
         f"paddock lab train {path}")


# ---------------------------------------------------------------------- entries
@entry_app.command("submit")
@handle_errors
def entry_submit(round_id: Annotated[str, typer.Argument(metavar="ROUND", help="Id de manche ou 'next'.")],
                 driver_id: Annotated[str, typer.Argument(metavar="DRIVER")],
                 brain: Annotated[Optional[int], typer.Option("--brain", help="Cerveau (défaut : actif).")] = None,
                 wing: Annotated[Optional[float], typer.Option("--wing", help="0 (peu d'appui) … 1 (max).")] = None,
                 brake_bias: Annotated[Optional[float], typer.Option("--brake-bias", help="0.50 … 0.66.")] = None,
                 start: Annotated[Optional[str], typer.Option("--start", help="Gomme de départ.")] = None,
                 quali: Annotated[Optional[str], typer.Option("--quali", help="Gomme de qualification.")] = None,
                 json_out: JsonOpt = False) -> None:
    """Inscrit un pilote à une manche avec un cerveau et un réglage."""
    be = backend()
    rid = _resolve_round(be, round_id)
    setup = {k: v for k, v in (("wing", wing), ("brake_bias", brake_bias), ("start_compound", start),
                                ("quali_compound", quali)) if v is not None}
    res = be.submit_entry(rid, driver_id, brain, setup)
    emit(res, json_out, f"Inscrit : {res['driver_id']} manche {res['round']} (id {res['round_id']}) avec le "
                        f"cerveau {res['brain_id']}, réglage {res['setup']}.")


if __name__ == "__main__":
    app()
