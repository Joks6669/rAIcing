"""Exhibition races: upload, validation, run, replays (API level)."""
from __future__ import annotations

import base64
import io
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from raicing.api.app import create_app
from raicing.api.settings import ApiSettings
from raicing.brains.spec import mlp_brain


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(ApiSettings(data_dir=tmp_path)))


def _package(outputs: int = 2) -> str:
    import numpy as np
    return base64.b64encode(mlp_brain([16], "tanh", outputs, rng=np.random.default_rng(0)).to_bytes()).decode()


def test_match_runs_and_serves_replays(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/matches", json={"name": "Essai", "laps": 1, "track_seed": 7, "fill": 1,
                                     "entrants": [{"name": "Réseau", "package": _package()},
                                                  {"reference": "prudent"}]})
    assert r.status_code == 201, r.text
    m = r.json()
    assert [e["name"] for e in m["entrants"]] == ["Réseau", "Prudent", "Pro"]
    assert m["entrants"][0]["brain"]["outputs"] == 2
    for _ in range(240):
        m = c.get(f"/api/matches/{m['id']}").json()
        if m["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert m["status"] == "done", m["error"]
    assert len(m["results"]["race"]) == 3 and len(m["results"]["quali"]) == 3
    assert m["results"]["starts"]["m0"] == "hard"           # 2 outputs: cannot pit
    for session in ("race", "quali"):
        rep = c.get(f"/api/matches/{m['id']}/replay?session={session}")
        assert rep.status_code == 200 and rep.json()["format"] == "raicing-replay/1"
    assert c.get("/api/matches").json()[0]["id"] == m["id"]


def test_match_rejects_bad_packages(tmp_path):
    c = _client(tmp_path)
    bad = base64.b64encode(b"pas un zip").decode()
    r = c.post("/api/matches", json={"entrants": [{"name": "X", "package": bad}]})
    assert r.status_code == 400 and "illisible" in r.json()["detail"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("autre.txt", "x")
    r = c.post("/api/matches", json={"entrants": [{"name": "Y", "package": base64.b64encode(buf.getvalue()).decode()}]})
    assert r.status_code == 400 and "brain.json" in r.json()["detail"]
    assert c.post("/api/matches", json={"entrants": []}).status_code == 400
    assert c.post("/api/matches", json={"entrants": [{"reference": "inconnu"}]}).status_code == 400
    assert c.get("/api/matches/abcdefgh").status_code == 404
    assert c.get("/api/matches/../../etc").status_code == 404
