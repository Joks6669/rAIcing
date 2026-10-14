"""Track geometry: centreline, boundaries, arc length and fast local projection."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


def wrap_angle(a: np.ndarray | float) -> np.ndarray | float:
    return (a + np.pi) % (2.0 * np.pi) - np.pi


@dataclass
class Track:
    """A closed circuit sampled at a fixed arc-length resolution.

    Index 0 is the start/finish line. The racing direction is increasing index.
    ``normal`` points to the left of the racing direction.
    """

    seed: int
    points: np.ndarray       # (n, 2) centreline
    half_width: np.ndarray   # (n,)
    name: str = ""
    slow_drying: np.ndarray | None = None  # (n_cells,) bool
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        p = np.asarray(self.points, dtype=np.float64)
        self.points = p
        self.half_width = np.asarray(self.half_width, dtype=np.float64)
        n = len(p)
        self.n = n
        nxt = np.roll(p, -1, axis=0)
        seg = nxt - p
        self.seg_vec = seg
        self.seg_len = np.hypot(seg[:, 0], seg[:, 1])
        self.s = np.concatenate([[0.0], np.cumsum(self.seg_len)[:-1]])
        self.length = float(self.seg_len.sum())
        self.ds = self.length / n
        # tangent: central difference
        prv = np.roll(p, 1, axis=0)
        t = nxt - prv
        t /= np.hypot(t[:, 0], t[:, 1])[:, None]
        self.tangent = t
        self.normal = np.stack([-t[:, 1], t[:, 0]], axis=1)
        self.heading = np.arctan2(t[:, 1], t[:, 0])
        # signed curvature (positive = left turn)
        dh = wrap_angle(np.roll(self.heading, -1) - np.roll(self.heading, 1))
        self.curvature = dh / (2.0 * self.ds)
        self.left = p + self.normal * self.half_width[:, None]
        self.right = p - self.normal * self.half_width[:, None]
        seg_u = seg / np.maximum(self.seg_len[:, None], 1e-9)
        self.seg_unit = seg_u
        self.seg_normal = np.stack([-seg_u[:, 1], seg_u[:, 0]], axis=1)
        if self.slow_drying is None:
            self.slow_drying = np.zeros(self.n_wet_cells(), dtype=bool)

    # ------------------------------------------------------------------
    def n_wet_cells(self, cell: float = 10.0) -> int:
        return max(1, int(np.ceil(self.length / cell)))

    def index_at(self, s: np.ndarray | float) -> np.ndarray:
        s = np.mod(s, self.length)
        return (np.asarray(s) / self.ds).astype(np.int64) % self.n

    def width_at(self, s: np.ndarray | float) -> np.ndarray:
        return self.half_width[self.index_at(s)]

    def project_global(self, xy: np.ndarray) -> np.ndarray:
        """Nearest centreline index for arbitrary points (slow, used for initialisation)."""
        xy = np.atleast_2d(xy)
        d = ((xy[:, None, :] - self.points[None, :, :]) ** 2).sum(-1)
        return d.argmin(axis=1)

    def project(self, xy: np.ndarray, hint: np.ndarray, back: int = 6, fwd: int = 8):
        """Project points onto the centreline, searching around ``hint`` indices.

        Returns (idx, s, lateral, half_width_at_projection).
        ``s`` is the arc length in [0, length); ``lateral`` is signed (left positive).
        """
        offsets = np.arange(-back, fwd + 1)
        cand = (hint[:, None] + offsets[None, :]) % self.n           # (m, k)
        a = self.points[cand]                                         # (m, k, 2)
        u = self.seg_unit[cand]
        L = self.seg_len[cand]
        rel = xy[:, None, :] - a
        t = np.clip((rel * u).sum(-1), 0.0, L)
        foot = a + u * t[..., None]
        d2 = ((xy[:, None, :] - foot) ** 2).sum(-1)
        j = d2.argmin(axis=1)
        rows = np.arange(len(xy))
        idx = cand[rows, j]
        tt = t[rows, j]
        frac = tt / np.maximum(L[rows, j], 1e-9)
        s = self.s[idx] + tt
        nrm = self.seg_normal[idx]
        lat = ((xy - foot[rows, j]) * nrm).sum(-1)
        hw = self.half_width[idx] * (1.0 - frac) + self.half_width[(idx + 1) % self.n] * frac
        return idx, s, lat, hw

    def point_at(self, s: np.ndarray | float, lateral: np.ndarray | float = 0.0) -> np.ndarray:
        s = np.mod(np.asarray(s, dtype=np.float64), self.length)
        i = (s / self.ds).astype(np.int64) % self.n
        frac = (s - self.s[i]) / np.maximum(self.seg_len[i], 1e-9)
        p = self.points[i] + self.seg_vec[i] * frac[..., None]
        return p + self.normal[i] * np.asarray(lateral)[..., None]

    def heading_at(self, s: np.ndarray | float) -> np.ndarray:
        return self.heading[self.index_at(s)]

    # ------------------------------------------------------------------
    def speed_profile(self, mu: float, mass: float, cla: float, power: float, cda: float,
                      brake_decel: float = 30.0, rho: float = 1.2, vmax: float = 95.0,
                      lookahead_smooth: int = 3) -> np.ndarray:
        """Quasi-steady maximum speed along the centreline (used by reference drivers
        and by the stewards' brake-check test). Not used by the physics."""
        g = 9.81
        k = np.abs(self.curvature)
        if lookahead_smooth > 1:
            kern = np.ones(lookahead_smooth) / lookahead_smooth
            k = np.convolve(np.concatenate([k[-lookahead_smooth:], k, k[:lookahead_smooth]]), kern, "same")[lookahead_smooth:-lookahead_smooth]
        denom = mass * k - mu * 0.5 * rho * cla
        with np.errstate(divide="ignore", invalid="ignore"):
            v = np.where(denom > 1e-6, np.sqrt(mu * mass * g / np.maximum(denom, 1e-6)), vmax)
        v = np.minimum(v, vmax)
        n = self.n
        ds = self.ds
        # backward pass (braking), twice around for the closed loop
        for _ in range(2):
            for i in range(n - 1, -1, -1):
                j = (i + 1) % n
                cap = np.sqrt(v[j] ** 2 + 2.0 * brake_decel * ds)
                if cap < v[i]:
                    v[i] = cap
        # forward pass (acceleration limited by power and grip)
        for _ in range(2):
            for i in range(n):
                j = (i + 1) % n
                vi = max(v[i], 5.0)
                drag = 0.5 * rho * cda * vi * vi
                a = min(power / (mass * vi), mu * g) - drag / mass
                cap = np.sqrt(max(vi * vi + 2.0 * max(a, 0.0) * ds, 0.0))
                if cap < v[j]:
                    v[j] = cap
        return v

    # ------------------------------------------------------------------
    def to_dict(self, decimate: int = 1) -> dict[str, Any]:
        sl = slice(None, None, decimate)
        return {
            "seed": int(self.seed),
            "name": self.name,
            "length": round(self.length, 2),
            "centerline": np.round(self.points[sl], 2).tolist(),
            "half_width": np.round(self.half_width[sl], 2).tolist(),
            "slow_drying": self.slow_drying.astype(int).tolist(),
            "pit_entry_s": round(self.length - self.meta.get("pit_entry_before_line", 50.0), 2),
            "pit_exit_s": self.meta.get("pit_exit_after_line", 150.0),
            "meta": {k: v for k, v in self.meta.items() if isinstance(v, (int, float, str))},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Track":
        return cls(seed=d["seed"], points=np.array(d["centerline"]), half_width=np.array(d["half_width"]),
                   name=d.get("name", ""), slow_drying=np.array(d.get("slow_drying", []), dtype=bool) if d.get("slow_drying") else None,
                   meta=d.get("meta", {}))
