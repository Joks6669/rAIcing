"""Observations given to the pilots (observation format version 1).

Everything is expressed in the car's frame and roughly normalised to [-1, 1]. The layout is
documented in ``OBS_LAYOUT`` and in docs/SPEC.md. Pilots only ever see this vector: no
global map, no knowledge of the future weather, no access to the other cars' brains.
"""
from __future__ import annotations

import numpy as np

from .world import FINISHED, PIT, RACING, World

OBS_VERSION = 1
LOOKAHEAD = np.array([5, 10, 15, 20, 30, 40, 50, 65, 80, 100, 125, 150, 180, 210, 240, 270], dtype=np.float64)
N_OPP = 6
OPP_RANGE = 150.0

OBS_LAYOUT: list[tuple[str, int]] = [
    ("own", 20),
    ("track", len(LOOKAHEAD) * 4),
    ("weather", 6),
    ("race", 10),
    ("opponents", N_OPP * 8),
    ("teammate", 9),
]
OBS_DIM = sum(n for _, n in OBS_LAYOUT)


def obs_slices() -> dict[str, slice]:
    out, k = {}, 0
    for name, n in OBS_LAYOUT:
        out[name] = slice(k, k + n)
        k += n
    return out


def observe(w: World, cars: np.ndarray | None = None) -> np.ndarray:
    """Return the (len(cars), OBS_DIM) float32 observation matrix."""
    tr = w.track
    if cars is None:
        cars = np.arange(w.n)
    cars = np.asarray(cars, dtype=np.int64)
    m = len(cars)
    o = np.zeros((m, OBS_DIM), dtype=np.float64)
    S = w.S
    x, y, psi = S[cars, 0], S[cars, 1], S[cars, 2]
    vx, vy, r = S[cars, 3], S[cars, 4], S[cars, 5]
    v = np.hypot(vx, vy)
    idx = w.idx[cars]
    c, s = np.cos(psi), np.sin(psi)
    P = w.PRM
    from .physics.kernels import PC
    k = 0
    # ---- own car (20)
    herr = (psi - tr.heading[idx] + np.pi) % (2 * np.pi) - np.pi
    topt, twid = P[cars, PC["c_t_opt"]], P[cars, PC["c_t_width"]]
    hw = np.maximum(w.hw[cars], 1.0)
    own = [
        v / 80.0, vx / 80.0, vy / 10.0, r / 2.0,
        w.steer[cars] / P[cars, PC["max_steer"]], w.pedal[cars],
        w.lat[cars] / hw, hw / 10.0, np.sin(herr), np.cos(herr),
        (w.temp_f[cars] - topt) / twid, (w.temp_r[cars] - topt) / twid,
        w.wear_f[cars], w.wear_r[cars], w.damage[cars],
    ]
    for j, col in enumerate(own):
        o[:, k + j] = col
    comp = w.compound[cars]
    o[np.arange(m), k + 15 + comp] = 1.0
    o[:, k + 19] = (w.ghost[cars] > 0).astype(np.float64)
    k += 20
    # ---- track preview (16 x 4)
    steps = np.round(LOOKAHEAD / tr.ds).astype(np.int64)
    pi = (idx[:, None] + steps[None, :]) % tr.n                  # (m, 16)
    dx = tr.points[pi, 0] - x[:, None]
    dy = tr.points[pi, 1] - y[:, None]
    lx = dx * c[:, None] + dy * s[:, None]
    ly = -dx * s[:, None] + dy * c[:, None]
    nl = len(LOOKAHEAD)
    o[:, k:k + nl] = lx / 100.0
    o[:, k + nl:k + 2 * nl] = ly / 100.0
    o[:, k + 2 * nl:k + 3 * nl] = tr.half_width[pi] / 10.0
    o[:, k + 3 * nl:k + 4 * nl] = np.clip(tr.curvature[pi] * 50.0, -3.0, 3.0)
    k += 4 * nl
    # ---- weather (6)
    sc = w.s[cars]
    o[:, k + 0] = w.wet_at_car[cars]
    o[:, k + 1] = w.wetmap.at(sc + 50.0, tr.length)
    o[:, k + 2] = w.wetmap.at(sc + 100.0, tr.length)
    o[:, k + 3] = w.wetmap.at(sc + 200.0, tr.length)
    o[:, k + 4] = w.rain
    o[:, k + 5] = (w.track_temp - 25.0) / 15.0
    k += 6
    # ---- race (10)
    kind = w.session.kind
    o[:, k + {"race": 0, "quali": 1, "lab": 2}.get(kind, 2)] = 1.0
    L = tr.length
    laps_done = np.floor(w.progress[cars] / L)
    if kind == "race":
        o[:, k + 3] = np.clip(w.session.laps - laps_done, 0, 30) / 15.0
    o[:, k + 4] = sc / L
    o[:, k + 5] = w.position[cars] / max(w.n - 1, 1)
    order = np.argsort(w.position)
    ahead_gap = np.zeros(m)
    behind_gap = np.zeros(m)
    for row, i in enumerate(cars):
        p = w.position[i]
        vi = max(float(np.hypot(w.vx[i], w.vy[i])), 10.0)
        if p > 0:
            j = order[p - 1]
            ahead_gap[row] = (w.progress[j] - w.progress[i]) / vi
        if p < w.n - 1:
            j = order[p + 1]
            if w.status[j] in (RACING, PIT):
                behind_gap[row] = (w.progress[i] - w.progress[j]) / vi
    o[:, k + 6] = np.clip(ahead_gap, 0, 5) / 5.0
    o[:, k + 7] = np.clip(behind_gap, 0, 5) / 5.0
    pe = L - w.regs.race.pit_entry_before_line
    o[:, k + 8] = ((pe - sc) % L) / L
    o[:, k + 9] = ((sc >= pe) & (sc < L - 2.0)).astype(np.float64)
    k += 10
    # ---- opponents (6 x 8)
    if w.session.interactions and w.n > 1:
        alive = (w.status == RACING) & (w.ghost <= 0.0)
        ds_all = (w.s[None, :] - sc[:, None] + 0.5 * L) % L - 0.5 * L   # (m, n)
        valid = alive[None, :] & (np.abs(ds_all) < OPP_RANGE)
        valid[np.arange(m), cars] = False
        dist = np.where(valid, np.abs(ds_all), np.inf)
        near = np.argsort(dist, axis=1)[:, :N_OPP]
        cvx, cvy = vx * c - vy * s, vx * s + vy * c                    # world velocity of observer
        for slot in range(near.shape[1]):   # fewer than N_OPP columns when w.n <= N_OPP
            j = near[:, slot]
            ok = np.isfinite(dist[np.arange(m), j])
            if not ok.any():
                continue
            ddx = w.x[j] - x
            ddy = w.y[j] - y
            jc, js = np.cos(w.psi[j]), np.sin(w.psi[j])
            jvx = w.vx[j] * jc - w.vy[j] * js
            jvy = w.vx[j] * js + w.vy[j] * jc
            rvx, rvy = jvx - cvx, jvy - cvy
            base = k + slot * 8
            o[:, base + 0] = np.where(ok, (ddx * c + ddy * s) / 50.0, 0.0)
            o[:, base + 1] = np.where(ok, (-ddx * s + ddy * c) / 50.0, 0.0)
            o[:, base + 2] = np.where(ok, (rvx * c + rvy * s) / 20.0, 0.0)
            o[:, base + 3] = np.where(ok, (-rvx * s + rvy * c) / 20.0, 0.0)
            o[:, base + 4] = np.where(ok, ds_all[np.arange(m), j] / OPP_RANGE, 0.0)
            o[:, base + 5] = np.where(ok, np.sin(w.psi[j] - psi), 0.0)
            o[:, base + 6] = np.where(ok, (w.teams[j] == w.teams[cars]).astype(np.float64), 0.0)
            o[:, base + 7] = ok.astype(np.float64)
    k += N_OPP * 8
    # ---- teammate (9)
    tm = w.teammate[cars]
    has = tm >= 0
    if has.any():
        j = np.where(has, tm, 0)
        live = has & np.isin(w.status[j], (RACING, PIT))
        o[:, k + 0] = live.astype(np.float64)
        dp = (w.progress[j] - w.progress[cars] + 0.5 * L) % L - 0.5 * L
        o[:, k + 1] = np.where(live, dp / (0.5 * L), 0.0)
        o[:, k + 2] = np.where(live, np.hypot(w.vx[j], w.vy[j]) / 80.0, 0.0)
        o[:, k + 3] = np.where(live, np.maximum(w.wear_f[j], w.wear_r[j]), 0.0)
        o[:, k + 4] = np.where(live, w.compound[j] / 3.0, 0.0)
        o[:, k + 5:k + 9] = np.where(has[:, None], w.messages[j], 0.0)
    k += 9
    assert k == OBS_DIM
    return np.nan_to_num(o, nan=0.0, posinf=5.0, neginf=-5.0).astype(np.float32)
