"""Run the API server: ``python -m raicing.api [--host 0.0.0.0] [--port 8000]``."""
from __future__ import annotations

import argparse
import os


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from .app import create_app
    from .settings import ApiSettings

    parser = argparse.ArgumentParser(description="Serveur HTTP de la ligue (docs/API.md)")
    parser.add_argument("--host", default=os.environ.get("RAICING_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("RAICING_PORT", "8000")))
    args = parser.parse_args(argv)
    app = create_app(ApiSettings.from_env())
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True)


if __name__ == "__main__":
    main()
