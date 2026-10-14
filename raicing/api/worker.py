"""League worker: ``python -m raicing.api.worker``.

Loop:

1. ``LeagueService.tick()`` — opens the next round's entries and runs it (qualifying + race, with
   the live feed) once its ``scheduled_at`` has passed;
2. runs the oldest queued job (training via ``raicing.lab.jobs.run_job``, or Driver Rating);
3. sleeps ``--interval`` seconds when there was nothing to do.

Uses the same environment as the API (``DATABASE_URL``, ``RAICING_DATA_DIR``, ``RAICING_*`` options).
"""
from __future__ import annotations

import argparse
import logging
import signal
import time
from types import FrameType

from ..league.db import Database
from ..league.service import LeagueError, LeagueService
from .settings import ApiSettings

log = logging.getLogger("raicing.worker")


class Worker:
    def __init__(self, settings: ApiSettings, rounds: bool = True, jobs: bool = True):
        self.settings = settings
        self.db = Database(settings.database_url, settings.data_dir)
        self.rounds = rounds
        self.jobs = jobs
        self.stopping = False

    def _service(self, session) -> LeagueService:
        return LeagueService(session, self.settings.data_dir, self.settings.options)

    def step(self) -> bool:
        """One iteration. Returns True if something was done."""
        did = False
        if self.rounds:
            try:
                with self.db.session() as s:
                    ran = self._service(s).tick()
                if ran:
                    log.info("manche(s) courue(s) : %s", ran)
                    did = True
            except LeagueError as exc:
                log.warning("manche non courue : %s", exc)
            except Exception:  # keep the worker alive
                log.exception("erreur pendant une manche")
        if self.jobs and not self.stopping:
            try:
                with self.db.session() as s:
                    svc = self._service(s)
                    job_id = svc.next_queued_job()
                    if job_id is not None:
                        log.info("job %s : démarrage", job_id)
                        out = svc.run_job_now(job_id)
                        log.info("job %s : %s", job_id, out["status"])
                        did = True
            except LeagueError as exc:
                log.warning("job non exécuté : %s", exc)
            except Exception:
                log.exception("erreur pendant un job")
        return did

    def run(self, once: bool = False) -> None:
        while not self.stopping:
            did = self.step()
            if once:
                return
            if not did:
                time.sleep(self.settings.worker_interval)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Worker de la ligue (manches programmées et entraînements)")
    parser.add_argument("--once", action="store_true", help="une seule itération puis sortie")
    parser.add_argument("--interval", type=float, default=None, help="pause (s) quand il n'y a rien à faire")
    parser.add_argument("--no-rounds", action="store_true", help="ne pas courir les manches programmées")
    parser.add_argument("--no-jobs", action="store_true", help="ne pas exécuter les entraînements")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = ApiSettings.from_env()
    if args.interval is not None:
        settings.worker_interval = args.interval
    worker = Worker(settings, rounds=not args.no_rounds, jobs=not args.no_jobs)

    def _stop(_sig: int, _frame: FrameType | None) -> None:
        log.info("arrêt demandé : fin après l'étape en cours")
        worker.stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker démarré (données : %s)", settings.data_dir)
    worker.run(once=args.once)


if __name__ == "__main__":
    main()
