"""Procedural circuit generator.

Tracks are closed smooth loops built from random control points, scaled to a target length,
checked for minimum corner radius and for self-proximity, then rotated so the start/finish
line sits on the longest straight.
"""
from __future__ import annotations

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.spatial import cKDTree

from ..config import TrackRules
from .geometry import Track

_SYL_A = ["Val", "Mont", "Roche", "Bel", "Clair", "Fonte", "Haute", "Vert", "Grand", "Saint", "Pierre", "Lune",
          "Marais", "Forge", "Sable", "Cendre", "Brume", "Givre", "Orme", "Chêne", "Ardoise", "Rive", "Corbel"]
_SYL_B = ["mont", "fort", "ville", "court", "bois", "lac", "val", "roc", "pré", "mare", "lande", "croix",
          "combe", "puy", "brune", "fontaine", "rive", "haye", "sart", "noue"]


def track_name(seed: int) -> str:
    rng = np.random.default_rng(seed ^ 0x5EED)
    return f"Circuit de {rng.choice(_SYL_A)}{rng.choice(_SYL_B)}"


def _resample_closed(pts: np.ndarray, spacing: float) -> np.ndarray:
    seg = np.diff(np.vstack([pts, pts[:1]]), axis=0)
    L = np.hypot(seg[:, 0], seg[:, 1])
    s = np.concatenate([[0.0], np.cumsum(L)])
    total = s[-1]
    n = max(16, int(round(total / spacing)))
    target = np.linspace(0.0, total, n, endpoint=False)
    closed = np.vstack([pts, pts[:1]])
    x = np.interp(target, s, closed[:, 0])
    y = np.interp(target, s, closed[:, 1])
    return np.stack([x, y], axis=1)


def _smooth_closed_curve(ctrl: np.ndarray, samples: int = 4000) -> np.ndarray:
    closed = np.vstack([ctrl, ctrl[:1]])
    d = np.hypot(*np.diff(closed, axis=0).T)
    t = np.concatenate([[0.0], np.cumsum(np.sqrt(d))])  # centripetal parametrisation
    cs = CubicSpline(t, closed, bc_type="periodic")
    tt = np.linspace(0.0, t[-1], samples, endpoint=False)
    return cs(tt)


def _curvature(pts: np.ndarray) -> np.ndarray:
    nxt = np.roll(pts, -1, axis=0)
    prv = np.roll(pts, 1, axis=0)
    a = np.hypot(*(pts - prv).T)
    b = np.hypot(*(nxt - pts).T)
    c = np.hypot(*(nxt - prv).T)
    cross = (pts[:, 0] - prv[:, 0]) * (nxt[:, 1] - prv[:, 1]) - (pts[:, 1] - prv[:, 1]) * (nxt[:, 0] - prv[:, 0])
    return 2.0 * cross / np.maximum(a * b * c, 1e-9)


def _laplacian_smooth(pts: np.ndarray, iters: int, alpha: float = 0.35) -> np.ndarray:
    p = pts.copy()
    for _ in range(iters):
        p = p + alpha * (0.5 * (np.roll(p, 1, 0) + np.roll(p, -1, 0)) - p)
    return p


def _self_clearance_ok(pts: np.ndarray, hw: np.ndarray, spacing: float, margin: float = 12.0) -> bool:
    n = len(pts)
    tree = cKDTree(pts)
    radius = 2.0 * hw.max() + margin
    pairs = tree.query_pairs(radius, output_type="ndarray")
    if len(pairs) == 0:
        return True
    i, j = pairs[:, 0], pairs[:, 1]
    gap = np.abs(i - j)
    gap = np.minimum(gap, n - gap) * spacing
    far = gap > (radius * 2.2)
    if not far.any():
        return True
    i, j = i[far], j[far]
    dist = np.hypot(*(pts[i] - pts[j]).T)
    need = hw[i] + hw[j] + margin
    return bool((dist >= need).all())


def generate_track(seed: int, rules: TrackRules | None = None, max_attempts: int = 200) -> Track:
    """Generate a deterministic track from ``seed``."""
    rules = rules or TrackRules()
    master = np.random.default_rng(seed)
    for attempt in range(max_attempts):
        rng = np.random.default_rng(master.integers(0, 2**63 - 1))
        n_ctrl = int(rng.integers(9, 17))
        angles = np.sort(rng.uniform(0, 2 * np.pi, n_ctrl))
        # enforce minimum angular spacing
        angles = np.linspace(0, 2 * np.pi, n_ctrl, endpoint=False) * 0.55 + angles * 0.45
        angles = np.sort(angles % (2 * np.pi))
        radii = rng.uniform(0.45, 1.0, n_ctrl)
        # occasional deep notch for hairpins
        if rng.random() < 0.6:
            k = rng.integers(0, n_ctrl)
            radii[k] *= rng.uniform(0.35, 0.6)
        aspect = rng.uniform(0.7, 1.35)
        ctrl = np.stack([np.cos(angles) * radii * aspect, np.sin(angles) * radii], axis=1)
        curve = _smooth_closed_curve(ctrl)
        target_len = rng.uniform(rules.min_length, rules.max_length)
        seg = np.diff(np.vstack([curve, curve[:1]]), axis=0)
        cur_len = np.hypot(seg[:, 0], seg[:, 1]).sum()
        curve *= target_len / cur_len
        pts = _resample_closed(curve, rules.resolution)
        # soften corners until min radius respected (bounded effort)
        ok_radius = False
        for _ in range(12):
            k = np.abs(_curvature(pts))
            if k.max() <= 1.0 / rules.min_radius:
                ok_radius = True
                break
            pts = _laplacian_smooth(pts, 40)
            pts = _resample_closed(pts, rules.resolution)
        if not ok_radius:
            continue
        seg = np.diff(np.vstack([pts, pts[:1]]), axis=0)
        length = np.hypot(seg[:, 0], seg[:, 1]).sum()
        if not (rules.min_length * 0.85 <= length <= rules.max_length * 1.1):
            continue
        n = len(pts)
        # width profile: smooth low-frequency variation
        s_norm = np.arange(n) / n
        base = rng.uniform(rules.min_width + 1.0, rules.max_width - 1.5)
        w = np.full(n, base)
        for h in range(1, 4):
            w += rng.uniform(-1.2, 1.2) * np.sin(2 * np.pi * (h * s_norm + rng.random()))
        w = np.clip(w, rules.min_width, rules.max_width)
        hw = w / 2.0
        if not _self_clearance_ok(pts, hw, rules.resolution):
            continue
        # random direction
        if rng.random() < 0.5:
            pts = pts[::-1].copy()
            hw = hw[::-1].copy()
        # start line: middle of the straightest 400 m window, line placed 120 m into it
        k = np.abs(_curvature(pts))
        win = int(400 / rules.resolution)
        kk = np.concatenate([k, k[:win]])
        csum = np.cumsum(kk)
        window_sum = csum[win:] - csum[:-win]
        start_window = int(np.argmin(window_sum[:n]))
        start = (start_window + int(220 / rules.resolution)) % n
        pts = np.roll(pts, -start, axis=0)
        hw = np.roll(hw, -start)
        # slow-drying zones (shade)
        n_cells = max(1, int(np.ceil(length / rules.wet_cell)))
        slow = np.zeros(n_cells, dtype=bool)
        for _ in range(int(rng.integers(1, 4))):
            c0 = int(rng.integers(0, n_cells))
            span = int(rng.integers(max(2, n_cells // 20), max(3, n_cells // 8)))
            slow[np.arange(c0, c0 + span) % n_cells] = True
        track = Track(seed=seed, points=pts, half_width=hw, name=track_name(seed), slow_drying=slow,
                      meta={"attempts": attempt + 1, "corners": int(_count_corners(k))})
        return track
    raise RuntimeError(f"could not generate a valid track for seed {seed}")


def _count_corners(k: np.ndarray, thresh: float = 1 / 120.0) -> int:
    on = k > thresh
    return int(np.sum(on & ~np.roll(on, 1)))
