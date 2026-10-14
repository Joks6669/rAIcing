"""Reference drivers for the lab, with cached racing lines and speed profiles.

``ReferenceDriver.__init__`` recomputes the racing line and nine speed profiles *per car*, which
costs ~0.15 s per car. The lab creates many drivers (teachers for imitation, opponents in traffic
worlds, track rotations), so this subclass memoises those pure functions of (track, car, personality).
The driving logic (``act``) is inherited unchanged.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from ..drivers.racing_line import line_curvature, racing_line, speed_profile_from_curvature
from ..drivers.reference import PERSONALITIES, ReferenceDriver
from ..world import World

_LINE_CACHE: dict[tuple, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
_PROFILE_CACHE: dict[tuple, np.ndarray] = {}
_MAX_CACHE = 256


def _track_key(world: World) -> tuple:
    tr = world.track
    return (int(tr.seed), int(tr.n), round(float(tr.length), 3), round(float(tr.points[0, 0]), 3),
            round(float(tr.points[0, 1]), 3))


def _line(world: World) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    key = _track_key(world)
    hit = _LINE_CACHE.get(key)
    if hit is None:
        tr = world.track
        line = racing_line(tr)
        k = line_curvature(tr, line)
        lp = tr.points + tr.normal * line[:, None]
        d = np.roll(lp, -1, axis=0) - np.roll(lp, 1, axis=0)
        heading = np.arctan2(d[:, 1], d[:, 0])
        if len(_LINE_CACHE) > _MAX_CACHE:
            _LINE_CACHE.clear()
        hit = (line, k, heading)
        _LINE_CACHE[key] = hit
    return hit


class CachedReferenceDriver(ReferenceDriver):
    """Drop-in ``ReferenceDriver`` whose expensive per-track precomputations are memoised."""

    def __init__(self, world: World, cars: Sequence[int], personalities: list[str] | str = "pro",
                 laps: int | None = None):
        self.w = world
        self.cars = [int(c) for c in cars]
        if isinstance(personalities, str):
            personalities = [personalities] * len(self.cars)
        self.pers = [PERSONALITIES[p] for p in personalities]
        tr = world.track
        self.line, self.line_k, self.line_heading = _line(world)
        self.laps = laps
        self.mu_grid = np.array([0.55, 0.7, 0.85, 1.0, 1.15, 1.3, 1.45, 1.6, 1.8])
        self.profiles = []
        tkey = _track_key(world)
        P = world.p
        for i, p in zip(self.cars, self.pers):
            key = (tkey, float(P["mass"][i]), float(P["cla"][i]), float(P["power"][i]), float(P["cda"][i]),
                   float(p.brake_use))
            grid = _PROFILE_CACHE.get(key)
            if grid is None:
                grid = np.stack([speed_profile_from_curvature(
                    self.line_k, tr.ds, mu=mu, mass=P["mass"][i], cla=P["cla"][i], power=P["power"][i],
                    cda=P["cda"][i], brake_decel=9.81 * mu * p.brake_use * 0.9) for mu in self.mu_grid])
                if len(_PROFILE_CACHE) > _MAX_CACHE:
                    _PROFILE_CACHE.clear()
                _PROFILE_CACHE[key] = grid
            self.profiles.append(grid)
        self.prev_steer = np.zeros(len(self.cars))
        self.pass_offset = np.zeros(len(self.cars))
        self.lat_target = np.full(len(self.cars), np.nan)

    def reset_rows(self, rows: Sequence[int] | np.ndarray) -> None:
        """Forget the controller memory of the given rows (cars re-placed on the track)."""
        rows = np.asarray(rows, dtype=np.int64)
        self.prev_steer[rows] = 0.0
        self.pass_offset[rows] = 0.0
        self.lat_target[rows] = np.nan

    def sync_with_world(self) -> None:
        """Align the steering-smoothing memory with the command actually applied to each car.

        Needed when another controller (a learner) drove the car at the previous decision, so that
        the teacher's label is a function of the true state (DAgger)."""
        self.prev_steer[:] = self.w.cmd_steer[self.cars]
