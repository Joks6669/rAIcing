"""Approximate minimum-curvature racing line (elastic band within the track limits)."""
from __future__ import annotations

import numpy as np

from ..track.geometry import Track


def racing_line(track: Track, margin: float = 2.6, iters: int = 600, stencil: int = 6,
                alpha: float = 0.25) -> np.ndarray:
    """Return the lateral offset (left positive) of a smooth racing line at each centreline index."""
    n = track.n
    lat = np.zeros(n)
    lim = np.maximum(track.half_width - margin, 0.5)
    P, N = track.points, track.normal
    for _ in range(iters):
        pts = P + N * lat[:, None]
        avg = 0.5 * (np.roll(pts, stencil, axis=0) + np.roll(pts, -stencil, axis=0))
        move = ((avg - pts) * N).sum(axis=1)
        lat = np.clip(lat + alpha * move * 1.6, -lim, lim)
    # light smoothing of the offset itself
    k = np.ones(9) / 9.0
    ext = np.concatenate([lat[-9:], lat, lat[:9]])
    lat = np.convolve(ext, k, "same")[9:-9]
    return np.clip(lat, -lim, lim)


def line_curvature(track: Track, lat: np.ndarray) -> np.ndarray:
    pts = track.points + track.normal * lat[:, None]
    nxt = np.roll(pts, -2, axis=0)
    prv = np.roll(pts, 2, axis=0)
    a = np.hypot(*(pts - prv).T)
    b = np.hypot(*(nxt - pts).T)
    c = np.hypot(*(nxt - prv).T)
    cross = (pts[:, 0] - prv[:, 0]) * (nxt[:, 1] - prv[:, 1]) - (pts[:, 1] - prv[:, 1]) * (nxt[:, 0] - prv[:, 0])
    return 2.0 * cross / np.maximum(a * b * c, 1e-9)


def speed_profile_from_curvature(k: np.ndarray, ds: float, mu: float, mass: float, cla: float,
                                 power: float, cda: float, brake_decel: float, vmax: float = 95.0) -> np.ndarray:
    g, rho = 9.81, 1.2
    k = np.abs(k)
    denom = mass * k - mu * 0.5 * rho * cla
    with np.errstate(divide="ignore", invalid="ignore"):
        v = np.where(denom > 1e-6, np.sqrt(mu * mass * g / np.maximum(denom, 1e-6)), vmax)
    v = np.minimum(v, vmax)
    n = len(v)
    for _ in range(2):
        for i in range(n - 1, -1, -1):
            j = (i + 1) % n
            # braking capacity grows with downforce
            dec = brake_decel * (1.0 + 0.5 * rho * cla * v[j] ** 2 / (mass * g) * 0.6)
            cap = np.sqrt(v[j] ** 2 + 2.0 * dec * ds)
            if cap < v[i]:
                v[i] = cap
    for _ in range(2):
        for i in range(n):
            j = (i + 1) % n
            vi = max(v[i], 5.0)
            drag = 0.5 * rho * cda * vi * vi
            a = min(power / (mass * vi), mu * g * 0.9) - drag / mass
            cap = np.sqrt(max(vi * vi + 2.0 * max(a, 0.0) * ds, 0.0))
            if cap < v[j]:
                v[j] = cap
    return v
