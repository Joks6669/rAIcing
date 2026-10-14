"""Live race publication.

While a race runs, :class:`LivePublisher` (the ``progress_cb`` of ``run_race``) appends messages
to ``<data_dir>/live/<round_id>.jsonl`` and keeps ``<data_dir>/live/current.json`` up to date.
The API's ``WS /ws/live`` tails that file and relays it at real-time pace (``speed`` = 1.0):
the broadcast clock starts when the race starts (``started_at``), so a race simulated faster than
real time is still shown at the pace it was "driven".

Message lines (one JSON object per line):

* ``{"type": "init", "round_id": .., "replay": {...replay header without frames...}}``
* ``{"type": "frame", "frame": [t, [x, y, psi, speed, status, ...13 replay fields...], ...]}``
* ``{"type": "weather", "weather_frame": [t, rain, [wetness per 10 m cell]]}``
* ``{"type": "event", "event": {"t": .., "type": "pit_in|...", "car": 3, "entry_id": "..."}}``
* ``{"type": "end", "t": .., "classification": [{"pos", "entry_id", "driver", "team", "status"}]}``
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, TextIO

from .util import dumps

STATE_FILE = "current.json"
STALE_AFTER = 600.0       # seconds without update before an unfinished broadcast is considered dead


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dumps(data))
    os.replace(tmp, path)


class LivePublisher:
    """Writes the live feed of one race session (callable used as ``run_race(progress_cb=...)``)."""

    def __init__(self, live_dir: str | Path, speed: float = 1.0):
        self.dir = Path(live_dir)
        self.speed = float(speed)
        self.path: Path | None = None
        self.state: dict[str, Any] = {}
        self._frames = 0
        self._weather = 0
        self._events = 0
        self._entry_ids: list[str] = []

    # ------------------------------------------------------------------ lifecycle
    def start(self, round_id: int, season: int, number: int, header: dict[str, Any], session: str = "race") -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{round_id}.jsonl"
        self._entry_ids = [c.get("entry_id", "") for c in header.get("cars", [])]
        with self.path.open("w", encoding="utf-8") as f:
            f.write(dumps({"type": "init", "round_id": round_id, "season": season, "round": number,
                           "session": session, "replay": header}) + "\n")
        now = time.time()
        self.state = {"round_id": round_id, "season": season, "round": number, "session": session,
                      "file": self.path.name, "started_at": now, "updated_at": now, "speed": self.speed,
                      "finished": False, "last_t": 0.0}
        _write_atomic(self.dir / STATE_FILE, self.state)

    def __call__(self, world: Any) -> None:
        self.flush(world.frames, world.weather_frames, world.events)

    def flush(self, frames: list, weather: list, events: list) -> None:
        """Append what is new in the (growing) frame, weather and event lists."""
        if self.path is None:
            return
        lines: list[str] = []
        # interleave in time order: weather frames and events are small, frames dominate
        for wf in weather[self._weather:]:
            lines.append(dumps({"type": "weather", "weather_frame": wf}))
        for ev in events[self._events:]:
            e = dict(ev)
            car = e.get("car")
            if isinstance(car, int) or hasattr(car, "__index__"):
                try:
                    e["entry_id"] = self._entry_ids[int(car)]
                except (IndexError, TypeError, ValueError):
                    pass
            lines.append(dumps({"type": "event", "event": e}))
        for fr in frames[self._frames:]:
            lines.append(dumps({"type": "frame", "frame": fr}))
        self._weather, self._events, self._frames = len(weather), len(events), len(frames)
        if lines:
            # sort by simulation time so that a reader can pace on a monotonic clock
            lines.sort(key=_line_time)
            with self.path.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        if frames:
            self.state["last_t"] = float(frames[-1][0])
        self.state["updated_at"] = time.time()
        _write_atomic(self.dir / STATE_FILE, self.state)

    def finish(self, replay: dict[str, Any] | None = None, classification: list[dict[str, Any]] | None = None,
               aborted: str | None = None) -> None:
        """Flush the tail of the session (from the final replay) and write the ``end`` message."""
        if self.path is None:
            return
        if replay is not None:
            self.flush(replay.get("frames", []), replay.get("weather_frames", []), replay.get("events", []))
        end: dict[str, Any] = {"type": "end", "t": self.state.get("last_t", 0.0)}
        if classification is not None:
            end["classification"] = [{k: r.get(k) for k in ("pos", "entry_id", "driver_id", "driver", "team",
                                                             "status", "points")} for r in classification]
        if aborted:
            end["aborted"] = aborted
        with self.path.open("a", encoding="utf-8") as f:
            f.write(dumps(end) + "\n")
        self.state.update(finished=True, updated_at=time.time())
        _write_atomic(self.dir / STATE_FILE, self.state)


def _line_time(line: str) -> float:
    d = json.loads(line)
    if d["type"] == "frame":
        return float(d["frame"][0])
    if d["type"] == "weather":
        return float(d["weather_frame"][0])
    return float(d["event"].get("t", 0.0))


# ---------------------------------------------------------------------- readers
def read_state(live_dir: str | Path) -> dict[str, Any] | None:
    p = Path(live_dir) / STATE_FILE
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def broadcast_time(state: dict[str, Any], now: float | None = None) -> float:
    """Broadcast clock (simulation seconds) of a live state."""
    now = time.time() if now is None else now
    return max(0.0, (now - float(state["started_at"])) * float(state.get("speed", 1.0) or 1.0))


def is_live(state: dict[str, Any] | None, now: float | None = None) -> bool:
    if not state:
        return False
    now = time.time() if now is None else now
    if not state.get("finished"):
        return now - float(state.get("updated_at", state["started_at"])) < STALE_AFTER
    return broadcast_time(state, now) < float(state.get("last_t", 0.0))


def live_status(live_dir: str | Path, now: float | None = None) -> dict[str, Any]:
    """Payload of ``GET /api/live``."""
    state = read_state(live_dir)
    if not is_live(state, now):
        return {"live": False}
    assert state is not None
    t = broadcast_time(state, now)
    t = min(t, float(state.get("last_t", t)))
    return {"live": True, "round_id": state["round_id"], "season": state.get("season"),
            "round": state.get("round"), "session": state.get("session", "race"), "t": round(t, 1)}


def embargoed_round(live_dir: str | Path, now: float | None = None) -> int | None:
    """Round whose results are hidden because its broadcast is still running."""
    state = read_state(live_dir)
    return int(state["round_id"]) if is_live(state, now) and state else None


def message_time(msg: dict[str, Any]) -> float | None:
    """Simulation time of a feed message (``None`` for init/end)."""
    kind = msg.get("type")
    if kind == "frame":
        return float(msg["frame"][0])
    if kind == "weather":
        return float(msg["weather_frame"][0])
    if kind == "event":
        return float(msg["event"].get("t", 0.0))
    return None


class FeedTail:
    """Incremental reader of a growing jsonl feed (only complete lines are returned)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fh: TextIO | None = None
        self._buf = ""

    def read(self) -> list[dict[str, Any]]:
        if self._fh is None:
            try:
                self._fh = self.path.open("r", encoding="utf-8")
            except OSError:
                return []
        chunk = self._fh.read()
        if not chunk:
            return []
        self._buf += chunk
        *complete, self._buf = self._buf.split("\n")
        out = []
        for line in complete:
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
