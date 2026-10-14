"""Reward computation from world deltas.

All terms are computed per learner car and per decision from the difference between the world
state before and after the decision (see docs/LAB.md, "reward"):

=========  ====================================================  ===================
term       quantity                                              sign of the weight
=========  ====================================================  ===================
progress   metres travelled along the lap / 100                   as given
time       1 per decision                                         as given (usually < 0)
crash      1 on elimination (wall, broken car, tyre, stall...)    always a penalty
wear       tyre wear consumed (mean of both axles, 1 = a tyre)    always a penalty
smooth     abs(change of the steering command)                    always a penalty
edge       how far inside the 1 m band along the edge (0..1)      always a penalty
finish     1 when the car takes the chequered flag                as given
position   places gained (traffic)                                as given
contact    1 per penalised at-fault contact (traffic)             always a penalty
=========  ====================================================  ===================

Penalty terms use ``-abs(weight)`` so that ``"crash": 20`` and ``"crash": -20`` mean the same thing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

import numpy as np

from ..world import DNF, DSQ, FINISHED, World

PENALTY_TERMS = ("crash", "wear", "smooth", "edge", "contact")
EDGE_BAND = 1.0          # metres: the "edge" penalty starts inside this band
MAX_PROGRESS_STEP = 20.0  # metres per decision; guards against teleports (placement, pit exit)


@dataclass
class RewardWeights:
    progress: float = 1.0
    time: float = -0.02
    crash: float = -20.0
    wear: float = -1.0
    smooth: float = 0.0
    edge: float = 0.0
    finish: float = 10.0
    position: float = 0.0
    contact: float = -5.0

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "RewardWeights":
        d = d or {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: float(v) for k, v in d.items() if k in known})

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    def signed(self, name: str) -> float:
        w = float(getattr(self, name))
        return -abs(w) if name in PENALTY_TERMS else w


@dataclass
class CarSnapshot:
    """The per-car quantities the reward is computed from."""
    progress: np.ndarray
    wear: np.ndarray
    position: np.ndarray
    status: np.ndarray
    at_fault: np.ndarray


def at_fault_contacts(world: World, cars: np.ndarray) -> np.ndarray:
    """Number of penalised at-fault contacts so far for each car of ``cars``."""
    out = np.zeros(len(cars), dtype=np.int64)
    if not world.incidents:
        return out
    row = {int(c): k for k, c in enumerate(cars)}
    for inc in world.incidents:
        if inc.fault is not None and inc.penalised and int(inc.fault) in row:
            out[row[int(inc.fault)]] += 1
    return out


def snapshot(world: World, cars: np.ndarray) -> CarSnapshot:
    cars = np.asarray(cars, dtype=np.int64)
    return CarSnapshot(
        progress=world.progress[cars].copy(),
        wear=0.5 * (world.wear_f[cars] + world.wear_r[cars]),
        position=world.position[cars].copy(),
        status=world.status[cars].copy(),
        at_fault=at_fault_contacts(world, cars),
    )


def edge_intrusion(world: World, cars: np.ndarray) -> np.ndarray:
    """0 when the car body is more than ``EDGE_BAND`` from the edge, rising to 1 at the edge."""
    cars = np.asarray(cars, dtype=np.int64)
    tr = world.track
    herr = (world.psi[cars] - tr.heading[world.idx[cars]] + np.pi) % (2 * np.pi) - np.pi
    half_w = 0.5 * world.p["width"][cars]
    half_l = 0.5 * world.p["length"][cars]
    extent = half_w * np.abs(np.cos(herr)) + half_l * np.abs(np.sin(herr))
    margin = world.hw[cars] - np.abs(world.lat[cars]) - extent
    return np.clip(1.0 - margin / EDGE_BAND, 0.0, 1.0)


def compute_rewards(weights: RewardWeights, world: World, cars: np.ndarray, before: CarSnapshot,
                    after: CarSnapshot, steer_delta: np.ndarray, eliminated: np.ndarray,
                    use_position: bool = False) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Reward of one decision for each car of ``cars``.

    ``eliminated`` flags cars eliminated during the decision, by the world (DNF/DSQ) or by the
    environment (stall, wrong way). Returns the total and the per-term breakdown."""
    cars = np.asarray(cars, dtype=np.int64)
    dprog = np.clip(after.progress - before.progress, -MAX_PROGRESS_STEP, MAX_PROGRESS_STEP)
    finished = (after.status == FINISHED) & (before.status != FINISHED)
    out_now = np.isin(after.status, (DNF, DSQ)) & ~np.isin(before.status, (DNF, DSQ))
    crashed = out_now | np.asarray(eliminated, dtype=bool)
    terms = {
        "progress": dprog / 100.0,
        "time": np.ones(len(cars)),
        "crash": crashed.astype(np.float64),
        "wear": np.maximum(after.wear - before.wear, 0.0),
        "smooth": np.abs(np.asarray(steer_delta, dtype=np.float64)),
        "edge": edge_intrusion(world, cars) if weights.edge else np.zeros(len(cars)),
        "finish": finished.astype(np.float64),
        "position": (before.position - after.position).astype(np.float64) if use_position else np.zeros(len(cars)),
        "contact": np.maximum(after.at_fault - before.at_fault, 0).astype(np.float64),
    }
    parts = {k: weights.signed(k) * v for k, v in terms.items()}
    total = np.zeros(len(cars))
    for v in parts.values():
        total += v
    return total, parts
