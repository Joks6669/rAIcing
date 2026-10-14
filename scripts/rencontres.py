#!/usr/bin/env python
"""Start the "Rencontres" interface locally: simulation API + website, then open the browser.

Works on Windows, macOS and Linux. Prerequisites: ``pip install -e ".[api]"`` (plus ``numba``),
Node.js 20+. The first launch installs and builds the website (a few minutes).

Usage::

    python scripts/rencontres.py                 # http://localhost:3000/rencontres
    python scripts/rencontres.py --data mes-rencontres --port 3000 --api-port 8000

Ctrl+C stops everything. Matches, models and replays are kept in the data folder.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def wait_for(url: str, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=ROOT / "rencontres_data", help="dossier des rencontres")
    ap.add_argument("--port", type=int, default=3000, help="port du site")
    ap.add_argument("--api-port", type=int, default=8000, help="port de l'API de simulation")
    ap.add_argument("--rebuild", action="store_true", help="reconstruire le site")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        print("Node.js (npm) est introuvable : installez Node.js 20 ou plus récent.", file=sys.stderr)
        return 1
    if a.rebuild or not (WEB / ".next" / "BUILD_ID").exists():
        print("Première installation du site (quelques minutes)…", flush=True)
        if not (WEB / "node_modules").exists():
            subprocess.run([npm, "ci"], cwd=WEB, check=True)
        subprocess.run([npm, "run", "build"], cwd=WEB, check=True, env={**os.environ, "RAICING_MOCK": "0"})

    a.data.mkdir(parents=True, exist_ok=True)
    api_env = {**os.environ, "RAICING_DATA_DIR": str(a.data.resolve())}
    api = subprocess.Popen([sys.executable, "-m", "raicing.api", "--port", str(a.api_port)], cwd=ROOT, env=api_env)
    web_env = {**os.environ, "RAICING_API_URL": f"http://127.0.0.1:{a.api_port}", "PORT": str(a.port)}
    web_env.pop("RAICING_MOCK", None)
    web = subprocess.Popen([npm, "start"], cwd=WEB, env=web_env)
    url = f"http://localhost:{a.port}/rencontres"
    try:
        if not wait_for(f"http://127.0.0.1:{a.api_port}/healthz", 60):
            print("L'API de simulation ne démarre pas (voir les messages ci-dessus).", file=sys.stderr)
            return 1
        if wait_for(url, 90):
            print(f"\nInterface prête : {url}\nCtrl+C pour arrêter.\n", flush=True)
            if not a.no_browser:
                webbrowser.open(url)
        while api.poll() is None and web.poll() is None:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in (web, api):
            if p.poll() is None:
                p.terminate()
        for p in (web, api):
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
