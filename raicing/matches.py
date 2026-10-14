"""Exhibition races ("rencontres") between uploaded brains, outside the championship.

Pilots are trained elsewhere; a match only needs each pilot's brain package (zip with
``brain.json`` + ``weights.npz``, see ``raicing.brains.spec``). The grid can be filled with
reference drivers. A match runs qualifying then the race on a procedural track and keeps both
replays. No quota, no garage: every car is the base car of the current regulations.

Storage (one directory per match under ``<data_dir>/matches/<id>/``):
``meta.json`` (settings, entrants, status, progress, results), ``brains/<k>.zip``,
``quali.json.gz`` and ``race.json.gz`` (replays, format ``raicing-replay/1``).
Matches run one at a time in a background thread (``MatchRunner``).
"""
from __future__ import annotations

import json
import queue
import re
import secrets
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from .brains.spec import Brain
from .config import COMPOUNDS, Regulations
from .drivers.reference import PERSONALITIES
from .league.service import save_json_gz, to_jsonable
from .physics.car import Setup
from .race import Entry, estimate_lap_time, race_laps, run_qualifying, run_race
from .sensors import OBS_DIM, OBS_VERSION
from .track import generate_track
from .weather import WeatherScript, generate_weather

MAX_ENTRANTS = 20
MAX_BRAINS = 12
MAX_PACKAGE_BYTES = 16 * 1024 * 1024
DEFAULT_COLORS = ["#E10600", "#00A3E0", "#FFC906", "#7CD321", "#B14CFF", "#FF7A00", "#00D2BE", "#F2F2F2",
                  "#FF4FA3", "#4A6CFF", "#C8F04A", "#9AA4B2"]
REF_NAMES = {"pro": "Pro", "rapide": "Rapide", "pneus": "Pneus", "cooperatif": "Coopératif",
             "prudent": "Prudent", "debutant": "Débutant"}
_ID = re.compile(r"^[a-z0-9]{8}$")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class MatchError(ValueError):
    """User-facing error (French), mapped to HTTP 400/404."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def check_brain(data: bytes, label: str) -> Brain:
    """Load and validate a brain package; raise ``MatchError`` with a clear message otherwise."""
    if len(data) > MAX_PACKAGE_BYTES:
        raise MatchError(f"{label} : fichier trop gros ({len(data) // 1024} Ko, maximum {MAX_PACKAGE_BYTES // 2**20} Mo)")
    try:
        brain = Brain.from_bytes(data)
    except Exception as exc:  # zip, json and npz errors
        raise MatchError(f"{label} : fichier illisible — attendu un .zip contenant brain.json et weights.npz ({exc})") from exc
    try:
        brain.validate()
    except Exception as exc:
        raise MatchError(f"{label} : modèle invalide — {exc}") from exc
    if int(brain.spec.get("input_dim", OBS_DIM)) != OBS_DIM or int(brain.spec.get("obs_version", OBS_VERSION)) != OBS_VERSION:
        raise MatchError(f"{label} : le modèle attend {brain.spec.get('input_dim')} entrées "
                         f"(observations v{brain.spec.get('obs_version')}), le simulateur en fournit {OBS_DIM} (v{OBS_VERSION})")
    return brain


def brain_summary(brain: Brain) -> dict[str, Any]:
    params = int(sum(int(w.size) for w in brain.weights.values()))
    out = int(brain.spec.get("output", {}).get("dim", 2))
    return {"params": params, "outputs": out, "can_pit": out >= 7, "radio": out >= 11,
            "method": brain.meta.get("method"), "layers": len(brain.spec.get("layers", []))}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class MatchStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "matches"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ files
    def _dir(self, match_id: str) -> Path:
        if not _ID.match(match_id or ""):
            raise MatchError("rencontre inconnue", 404)
        d = self.root / match_id
        if not (d / "meta.json").exists():
            raise MatchError("rencontre inconnue", 404)
        return d

    def read(self, match_id: str) -> dict[str, Any]:
        return json.loads((self._dir(match_id) / "meta.json").read_text(encoding="utf-8"))

    def write(self, meta: dict[str, Any]) -> None:
        d = self.root / meta["id"]
        tmp = d / "meta.json.tmp"
        tmp.write_text(json.dumps(to_jsonable(meta), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(d / "meta.json")

    def update(self, match_id: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            meta = self.read(match_id)
            meta.update(fields)
            self.write(meta)
            return meta

    def replay_path(self, match_id: str, session: str) -> Path:
        if session not in ("race", "quali"):
            raise MatchError("séance inconnue (race ou quali)")
        p = self._dir(match_id) / f"{session}.json.gz"
        if not p.exists():
            raise MatchError("replay pas encore disponible", 404)
        return p

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        metas = []
        for d in self.root.iterdir():
            f = d / "meta.json"
            if f.exists():
                try:
                    metas.append(json.loads(f.read_text(encoding="utf-8")))
                except ValueError:
                    continue
        metas.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return [public(m) for m in metas[:limit]]

    # ------------------------------------------------------------------ creation
    def create(self, body: dict[str, Any], packages: list[bytes | None]) -> dict[str, Any]:
        """``body``: {name, entrants: [{name, color?, start?, reference?}], track_seed?, laps?, weather?,
        fill?}; ``packages[k]`` holds entrant k's brain package (None for a reference driver)."""
        entrants_in = list(body.get("entrants") or [])
        if not entrants_in:
            raise MatchError("ajoutez au moins un participant")
        if len(entrants_in) != len(packages):
            raise MatchError("chaque participant doit avoir un modèle ou un pilote de référence")
        fill = int(body.get("fill") or 0)
        if fill < 0 or fill > MAX_ENTRANTS:
            raise MatchError(f"nombre de pilotes de référence invalide (0 à {MAX_ENTRANTS})")
        if len(entrants_in) + fill > MAX_ENTRANTS:
            raise MatchError(f"au plus {MAX_ENTRANTS} voitures sur la grille")
        if sum(p is not None for p in packages) > MAX_BRAINS:
            raise MatchError(f"au plus {MAX_BRAINS} modèles par rencontre")
        if len(entrants_in) + fill < 1:
            raise MatchError("la grille est vide")
        regs = Regulations()
        seed = body.get("track_seed")
        if seed in (None, "", "random"):
            seed = secrets.randbelow(10 ** 9)
        try:
            seed = int(seed)
        except (TypeError, ValueError) as exc:
            raise MatchError("graine de circuit invalide (nombre entier)") from exc
        weather = str(body.get("weather") or "dry")
        if weather not in ("dry", "rain", "random"):
            raise MatchError("météo invalide (dry, rain ou random)")
        laps = body.get("laps")
        if laps not in (None, ""):
            try:
                laps = int(laps)
            except (TypeError, ValueError) as exc:
                raise MatchError("nombre de tours invalide") from exc
            if not 1 <= laps <= 30:
                raise MatchError("nombre de tours : entre 1 et 30")
        else:
            laps = None

        entrants: list[dict[str, Any]] = []
        brains: list[Brain | None] = []
        for k, (e, pkg) in enumerate(zip(entrants_in, packages)):
            ref_key = str(e.get("reference") or "pro")
            default = REF_NAMES.get(ref_key, ref_key.capitalize()) if pkg is None else f"Pilote {k + 1}"
            name = str(e.get("name") or "").strip()[:40] or default
            color = str(e.get("color") or DEFAULT_COLORS[k % len(DEFAULT_COLORS)])
            if not _HEX.match(color):
                raise MatchError(f"{name} : couleur invalide (format #RRGGBB)")
            start = str(e.get("start") or "auto")
            if start != "auto" and start not in COMPOUNDS:
                raise MatchError(f"{name} : gomme de départ inconnue ({start})")
            row: dict[str, Any] = {"name": name, "color": color.upper(), "start": start}
            if pkg is None:
                ref = str(e.get("reference") or "pro")
                if ref not in PERSONALITIES:
                    raise MatchError(f"{name} : pilote de référence inconnu ({ref})")
                row.update(kind="reference", reference=ref)
                brains.append(None)
            else:
                brain = check_brain(pkg, name)
                row.update(kind="brain", brain=brain_summary(brain))
                brains.append(brain)
            entrants.append(row)
        pers = ["pro", "rapide", "pneus", "cooperatif", "prudent", "debutant"]
        for j in range(fill):
            p = pers[j % len(pers)]
            entrants.append({"name": REF_NAMES[p], "color": "#6B7380", "start": "auto",
                             "kind": "reference", "reference": p})
            brains.append(None)

        # distinct names (the TV graphics show the first three letters)
        seen: dict[str, int] = {}
        for row in entrants:
            n = seen.get(row["name"], 0) + 1
            seen[row["name"]] = n
            if n > 1:
                row["name"] = f"{row['name']} {n}"
        track = generate_track(seed, regs.track)
        nominal = race_laps(track, regs)
        match_id = secrets.token_hex(4)
        d = self.root / match_id
        (d / "brains").mkdir(parents=True)
        for k, pkg in enumerate(packages):
            if pkg is not None:
                (d / "brains" / f"{k}.zip").write_bytes(pkg)
        meta = {
            "id": match_id, "name": str(body.get("name") or "").strip()[:80] or f"Rencontre {match_id}",
            "created_at": _now(), "status": "queued", "progress": 0.0, "stage": "en file d'attente",
            "track": {"seed": seed, "name": track.name, "length": round(float(track.length), 1),
                      "corners": int(track.meta.get("corners", 0))},
            "laps": laps or nominal, "weather": weather, "entrants": entrants,
            "error": None, "results": None,
        }
        self.write(meta)
        return public(meta)


def public(meta: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in meta.items() if not k.startswith("_")}


# ---------------------------------------------------------------------- execution
def _start_compound(e: dict[str, Any], brain: Brain | None, laps: int, wet: bool) -> str:
    if e.get("start") and e["start"] != "auto":
        return e["start"]
    if wet:
        return "wet"
    if brain is not None and int(brain.spec.get("output", {}).get("dim", 2)) < 7:
        return "hard"     # cannot pit: the hard tyre lasts a whole race
    return "medium" if laps <= 13 else "hard"


def run_match(store: MatchStore, match_id: str) -> None:
    meta = store.read(match_id)
    d = store.root / match_id
    regs = Regulations()
    track = generate_track(int(meta["track"]["seed"]), regs.track)
    laps = int(meta["laps"])
    lap_est = estimate_lap_time(track, regs)
    wseed = int(meta["track"]["seed"]) % (2 ** 31 - 1)
    if meta["weather"] == "dry":
        weather = WeatherScript.dry(22.0)
    else:
        weather = generate_weather(wseed, laps * lap_est, regs.weather,
                                   force_rain=True if meta["weather"] == "rain" else None)
    wet_start = weather.initial_wetness > 0.3 or (weather.rain[0] if weather.rain else 0.0) > 0.4
    entries: list[Entry] = []
    for k, e in enumerate(meta["entrants"]):
        brain = Brain.from_bytes((d / "brains" / f"{k}.zip").read_bytes()) if e["kind"] == "brain" else None
        start = _start_compound(e, brain, laps, wet_start)
        team = "Modèle" if brain is not None else f"Pilote de référence ({PERSONALITIES[e['reference']].label})"
        entries.append(Entry(entry_id=f"m{k}", team_id=f"m{k}", team_name=team, driver_name=e["name"],
                             driver_id=f"m{k}", brain=brain, reference=e.get("reference"), color=e["color"],
                             setup=Setup(start_compound=start, quali_compound="wet" if wet_start else "soft")))
    store.update(match_id, status="running", stage="qualifications", progress=0.02, started_at=_now())
    quali = run_qualifying(track, entries, regs, weather, seed=wseed, record=True)
    grid = [int(r["car"]) for r in quali["classification"]]
    store.update(match_id, stage="course", progress=0.1)
    total = max(laps * lap_est, 1.0)

    def progress(w) -> None:
        store.update(match_id, progress=round(0.1 + 0.88 * min(w.t / (total * 1.1), 1.0), 3))

    race = run_race(track, entries, grid, laps, regs, weather, seed=wseed, record=True, progress_cb=progress)
    for kind, res in (("quali", quali), ("race", race)):
        rep = res.pop("replay", None)
        if rep is not None:
            save_json_gz(rep, d / f"{kind}.json.gz")

    def rows(res: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
        return [{k: r.get(k) for k in keys} for r in res["classification"]]

    results = {
        "quali": rows(quali, ("pos", "car", "driver", "time", "gap", "status", "reason", "compound")),
        "race": rows(race, ("pos", "car", "driver", "status", "reason", "laps", "time", "gap", "best_lap",
                            "pits", "penalty")),
        "grid": grid,
        "events": [e for e in race.get("events", []) if e.get("type") in ("contact", "out", "dsq", "penalty")],
        "weather": {"ambient": round(float(weather.ambient), 1),
                    "rain": bool(max(weather.rain) > 0.0 or weather.initial_wetness > 0.0)},
        "starts": {f"m{k}": e.setup.start_compound for k, e in enumerate(entries)},
    }
    store.update(match_id, status="done", stage="terminée", progress=1.0, finished_at=_now(),
                 results=to_jsonable(results))


class MatchRunner:
    """Runs queued matches one at a time in a daemon thread."""

    def __init__(self, store: MatchStore):
        self.store = store
        self.q: "queue.Queue[str]" = queue.Queue()
        self.thread = threading.Thread(target=self._loop, name="matches", daemon=True)
        self.thread.start()
        # matches interrupted by a restart are queued again
        for m in store.list(limit=1000):
            if m["status"] in ("queued", "running"):
                self.q.put(m["id"])

    def submit(self, match_id: str) -> None:
        self.q.put(match_id)

    def _loop(self) -> None:
        while True:
            match_id = self.q.get()
            try:
                run_match(self.store, match_id)
            except Exception as exc:  # keep the runner alive, report the failure on the match
                traceback.print_exc()
                try:
                    self.store.update(match_id, status="failed", stage="échec",
                                      error=f"{type(exc).__name__}: {exc}")
                except Exception:
                    pass
