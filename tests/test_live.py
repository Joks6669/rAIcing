"""Live feed publication and reading."""
from __future__ import annotations

import json
import time
import types

from raicing.league.live import (STATE_FILE, FeedTail, LivePublisher, embargoed_round, is_live, live_status,
                                 message_time, read_state)


def _world(frames, weather=(), events=()):
    return types.SimpleNamespace(frames=list(frames), weather_frames=list(weather), events=list(events))


def test_publisher_writes_ordered_feed_and_state(tmp_path):
    pub = LivePublisher(tmp_path, speed=1.0)
    header = {"format": "raicing-replay/1", "cars": [{"entry_id": "a#1"}, {"entry_id": "b#1"}]}
    pub.start(7, 1, 3, header)
    w = _world([[0.2, [0, 0]], [0.4, [1, 1]]], [[0.0, 0.1, [0.0]]], [{"t": 0.3, "type": "pit_in", "car": 1}])
    pub(w)
    w.frames.append([0.6, [2, 2]])
    w.events.append({"t": 0.5, "type": "contact", "cars": [0, 1]})
    pub(w)
    replay = {"frames": w.frames + [[0.8, [3, 3]]], "weather_frames": w.weather_frames, "events": w.events}
    pub.finish(replay, [{"pos": 1, "entry_id": "a#1", "driver": "A", "status": "finished", "points": 25}])
    lines = [json.loads(x) for x in (tmp_path / "7.jsonl").read_text().splitlines()]
    kinds = [m["type"] for m in lines]
    assert kinds[0] == "init" and kinds[-1] == "end" and kinds.count("frame") == 4
    times = [message_time(m) for m in lines[1:-1]]
    assert times == sorted(times)
    pit = next(m for m in lines if m["type"] == "event" and m["event"]["type"] == "pit_in")
    assert pit["event"]["entry_id"] == "b#1"
    assert lines[-1]["classification"][0]["entry_id"] == "a#1"
    state = read_state(tmp_path)
    assert state["finished"] and state["last_t"] == 0.8 and state["round_id"] == 7


def test_live_status_and_embargo(tmp_path):
    assert live_status(tmp_path) == {"live": False} and embargoed_round(tmp_path) is None
    now = time.time()
    state = {"round_id": 3, "season": 1, "round": 2, "session": "race", "file": "3.jsonl", "started_at": now - 10,
             "updated_at": now, "speed": 1.0, "finished": True, "last_t": 100.0}
    (tmp_path / STATE_FILE).write_text(json.dumps(state))
    st = live_status(tmp_path, now)
    assert st["live"] and st["round_id"] == 3 and 9.0 <= st["t"] <= 11.0
    assert embargoed_round(tmp_path, now) == 3
    assert not is_live(state, now + 200)                      # broadcast over
    state.update(finished=False, updated_at=now - 3600)       # dead publisher
    assert not is_live(state, now)


def test_feed_tail_returns_complete_lines_only(tmp_path):
    p = tmp_path / "f.jsonl"
    tail = FeedTail(p)
    assert tail.read() == []
    p.write_text('{"type": "init"}\n{"type": "fr')
    assert tail.read() == [{"type": "init"}]
    with p.open("a") as f:
        f.write('ame", "frame": [1.0]}\n')
    assert tail.read() == [{"type": "frame", "frame": [1.0]}]
    tail.close()
