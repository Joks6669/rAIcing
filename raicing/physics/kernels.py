"""JIT-compiled physics kernels.

All per-car physics lives here as plain loops so that numba can compile it. When numba is not
installed the same code runs as pure Python (correct but much slower).

State and parameters are packed in 2-D float64 arrays; the column indices are the constants
below. ``World`` exposes them as named views.
"""
from __future__ import annotations

import math

import numpy as np

try:  # pragma: no cover - exercised implicitly
    from numba import njit
    HAVE_NUMBA = True
except Exception:  # pragma: no cover
    HAVE_NUMBA = False

    def njit(*args, **kwargs):
        if len(args) == 1 and callable(args[0]):
            return args[0]

        def deco(f):
            return f
        return deco

G = 9.81
RHO = 1.2

# ---- state columns ---------------------------------------------------------
STATE_COLS = (
    "x", "y", "psi", "vx", "vy", "r", "steer", "pedal", "cmd_steer", "cmd_pedal", "ax_prev", "ay_prev",
    "temp_f", "temp_r", "wear_f", "wear_r", "damage", "s", "lat", "hw", "progress", "max_progress",
    "ghost", "stall_timer", "wrong_timer", "wet_at_car", "drag_mult", "df_mult", "idx",
    "cross_time", "slide_f", "slide_r",
)
SC = {name: i for i, name in enumerate(STATE_COLS)}
NS = len(STATE_COLS)

# ---- parameter columns -----------------------------------------------------
PARAM_COLS = (
    "mass", "iz", "lf", "lr", "h", "tw", "length", "width", "power", "max_drive", "max_brake",
    "brake_front", "cda", "cla", "aero_front", "roll_res", "max_steer", "steer_rate", "pedal_rate",
    "mu", "load_sens", "fz_nom", "pac_b", "pac_c", "heat_cap", "heat_gain", "cool_base",
    "cool_speed", "roll_heat", "wear_mult", "rear_grip", "rear_b", "wheelbase",
    "c_grip", "c_t_opt", "c_t_width", "c_wear_rate", "c_wear_shape", "c_wet_loss", "c_dry_overheat",
)
PC = {name: i for i, name in enumerate(PARAM_COLS)}
NP_ = len(PARAM_COLS)

# flags returned by the step kernel
F_WALL = 1
F_TYRE = 2
F_LAP = 4
F_STALL = 8
F_WRONG = 16

# local aliases (numba treats module-level ints as compile-time constants)
_X, _Y, _PSI, _VX, _VY, _R = 0, 1, 2, 3, 4, 5
_STEER, _PEDAL, _CS, _CP, _AXP, _AYP = 6, 7, 8, 9, 10, 11
_TF, _TR, _WF, _WR, _DMG = 12, 13, 14, 15, 16
_S, _LAT, _HW, _PROG, _MAXP = 17, 18, 19, 20, 21
_GHOST, _STALL, _WRONG, _WET, _DRAGM, _DFM, _IDX = 22, 23, 24, 25, 26, 27, 28
_CROSS, _SLF, _SLR = 29, 30, 31

_MASS, _IZ, _LF, _LR, _H, _TW, _LEN, _WID = 0, 1, 2, 3, 4, 5, 6, 7
_POW, _MAXD, _MAXB, _BF, _CDA, _CLA, _AEROF, _ROLL = 8, 9, 10, 11, 12, 13, 14, 15
_MAXST, _STRATE, _PEDRATE, _MU, _LS, _FZN, _PB, _PCC = 16, 17, 18, 19, 20, 21, 22, 23
_HCAP, _HGAIN, _CB, _CSP, _RH, _WMULT, _RG, _RB, _WB = 24, 25, 26, 27, 28, 29, 30, 31, 32
_CGRIP, _CTOPT, _CTW, _CWR, _CWS, _CWL, _CDO = 33, 34, 35, 36, 37, 38, 39


@njit(cache=True)
def _clip(v, lo, hi):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


@njit(cache=True)
def project_point(px, py, hint, back, fwd, pts, seg_unit, seg_len, seg_normal, cum_s, half_w, n):
    """Project one point onto the centreline around index ``hint``.

    Returns (idx, s, lateral, half_width)."""
    best_d2 = 1e30
    best_idx = hint
    best_t = 0.0
    best_fx = 0.0
    best_fy = 0.0
    for o in range(-back, fwd + 1):
        j = (hint + o) % n
        ax = pts[j, 0]
        ay = pts[j, 1]
        ux = seg_unit[j, 0]
        uy = seg_unit[j, 1]
        L = seg_len[j]
        t = (px - ax) * ux + (py - ay) * uy
        if t < 0.0:
            t = 0.0
        elif t > L:
            t = L
        fx = ax + ux * t
        fy = ay + uy * t
        d2 = (px - fx) * (px - fx) + (py - fy) * (py - fy)
        if d2 < best_d2:
            best_d2 = d2
            best_idx = j
            best_t = t
            best_fx = fx
            best_fy = fy
    j = best_idx
    L = seg_len[j]
    frac = best_t / L if L > 1e-9 else 0.0
    s = cum_s[j] + best_t
    lat = (px - best_fx) * seg_normal[j, 0] + (py - best_fy) * seg_normal[j, 1]
    hw = half_w[j] * (1.0 - frac) + half_w[(j + 1) % n] * frac
    return j, s, lat, hw


@njit(cache=True)
def aero_factors(S, active, slip_range, slip_cut, dirty_range, dirty_cut, wake_hw, length):
    n = S.shape[0]
    for i in range(n):
        S[i, _DRAGM] = 1.0
        S[i, _DFM] = 1.0
    for i in range(n):
        if not active[i] or S[i, _GHOST] > 0.0:
            continue
        best_slip = 0.0
        best_dirty = 0.0
        for j in range(n):
            if j == i or not active[j] or S[j, _GHOST] > 0.0:
                continue
            ds = (S[j, _S] - S[i, _S]) % length
            if ds <= 0.5 or ds >= slip_range:
                continue
            dl = abs(S[j, _LAT] - S[i, _LAT])
            latf = 1.0 - dl / wake_hw
            if latf <= 0.0:
                continue
            sl = (1.0 - ds / slip_range) * latf
            if sl > best_slip:
                best_slip = sl
            if ds < dirty_range:
                di = (1.0 - ds / dirty_range) * latf
                if di > best_dirty:
                    best_dirty = di
        S[i, _DRAGM] = 1.0 - slip_cut * best_slip
        S[i, _DFM] = 1.0 - dirty_cut * best_dirty


@njit(cache=True)
def _temp_factor(T, topt, tw):
    z = (T - topt) / tw
    return _clip(1.0 - 0.25 * z * z, 0.7, 1.0)


@njit(cache=True)
def _wear_factor(w, shape):
    return 1.0 - 0.28 * _clip(w, 0.0, 1.0) ** shape


@njit(cache=True)
def _axle_capacity(fz, dlat, ls, nom):
    half = 0.5 * fz
    d = dlat if dlat < half else half
    out = half + d
    inn = half - d
    mo = _clip(1.0 - ls * (out / nom - 1.0), 0.6, 1.3)
    mi = _clip(1.0 - ls * (inn / nom - 1.0), 0.6, 1.3)
    return mo * out + mi * inn


@njit(cache=True)
def step_cars(S, PRM, active, flags, dt, t_now, track_temp,
              pts, seg_unit, seg_len, seg_normal, cum_s, half_w, heading, n_pts, length,
              check_walls, stall_speed, wrong_angle):
    """One physics step for every active car. Writes flags for events to handle in Python."""
    ncar = S.shape[0]
    for i in range(ncar):
        flags[i] = 0
        if not active[i]:
            continue
        p = PRM[i]
        st = S[i]
        # ---- actuators
        dmax = p[_STRATE] * dt
        target = st[_CS] * p[_MAXST]
        st[_STEER] += _clip(target - st[_STEER], -dmax, dmax)
        pmax = p[_PEDRATE] * dt
        st[_PEDAL] += _clip(st[_CP] - st[_PEDAL], -pmax, pmax)
        vx = st[_VX]
        vy = st[_VY]
        r = st[_R]
        v = math.sqrt(vx * vx + vy * vy)
        dmg = st[_DMG]
        q = 0.5 * RHO * v * v
        downforce = q * p[_CLA] * st[_DFM] * (1.0 - 0.35 * dmg)
        drag = q * p[_CDA] * st[_DRAGM] * (1.0 + 0.25 * dmg)
        m = p[_MASS]
        L = p[_WB]
        lf = p[_LF]
        lr = p[_LR]
        stat_f = m * G * lr / L
        stat_r = m * G * lf / L
        dlong = m * st[_AXP] * p[_H] / L
        fzf = stat_f + downforce * p[_AEROF] - dlong
        if fzf < 0.15 * stat_f:
            fzf = 0.15 * stat_f
        fzr = stat_r + downforce * (1.0 - p[_AEROF]) + dlong
        if fzr < 0.15 * stat_r:
            fzr = 0.15 * stat_r
        wet = st[_WET]
        base = p[_MU] * p[_CGRIP] * (1.0 - p[_CWL] * wet)
        gf = base * _temp_factor(st[_TF], p[_CTOPT], p[_CTW]) * _wear_factor(st[_WF], p[_CWS])
        gr = base * _temp_factor(st[_TR], p[_CTOPT], p[_CTW]) * _wear_factor(st[_WR], p[_CWS])
        dlat = m * abs(st[_AYP]) * p[_H] / p[_TW]
        share_f = fzf / (fzf + fzr)
        fmax_f = gf * _axle_capacity(fzf, dlat * share_f, p[_LS], p[_FZN])
        fmax_r = gr * p[_RG] * _axle_capacity(fzr, dlat * (1.0 - share_f), p[_LS], p[_FZN])
        if fmax_f < 1.0:
            fmax_f = 1.0
        if fmax_r < 1.0:
            fmax_r = 1.0
        # ---- slip angles and lateral forces
        vxs = vx if vx > 0.5 else 0.5
        steer = st[_STEER]
        af = math.atan2(vy + lf * r, vxs) - steer
        ar = math.atan2(vy - lr * r, vxs)
        B = p[_PB]
        C = p[_PCC]
        fyf = -fmax_f * math.sin(C * math.atan(B * af))
        fyr = -fmax_r * math.sin(C * math.atan(B * p[_RB] * ar))
        # ---- longitudinal
        ped = st[_PEDAL]
        thr = ped if ped > 0.0 else 0.0
        brk = -ped if ped < 0.0 else 0.0
        vxd = vx if vx > 1.0 else 1.0
        fdrive = p[_POW] / vxd
        if fdrive > p[_MAXD]:
            fdrive = p[_MAXD]
        drive = thr * fdrive
        moving = 1.0 if vx > 0.05 else 0.0
        brake = brk * p[_MAXB] * moving
        fxf_d = -brake * p[_BF]
        fxr_d = drive - brake * (1.0 - p[_BF])
        if abs(fxf_d) > fmax_f:
            fxf = math.copysign(0.95 * fmax_f, fxf_d)
        else:
            fxf = fxf_d
        if abs(fxr_d) > fmax_r:
            fxr = math.copysign(0.95 * fmax_r, fxr_d)
        else:
            fxr = fxr_d
        exc_f = abs(fxf_d) - abs(fxf)
        exc_r = abs(fxr_d) - abs(fxr)
        kf = 1.0 - (fxf / fmax_f) ** 2
        kr = 1.0 - (fxr / fmax_r) ** 2
        fyf *= math.sqrt(kf if kf > 0.0 else 0.0)
        fyr *= math.sqrt(kr if kr > 0.0 else 0.0)
        cs = math.cos(steer)
        sn = math.sin(steer)
        roll = p[_ROLL] * moving
        fx = fxr + fxf * cs - fyf * sn - drag - roll
        fy = fyr + fxf * sn + fyf * cs
        mz = lf * (fyf * cs + fxf * sn) - lr * fyr
        vx_n = vx + (fx / m + r * vy) * dt
        vy_n = vy + (fy / m - r * vx) * dt
        r_n = r + mz / p[_IZ] * dt
        if vx_n < 0.0:
            vx_n = 0.0
        b = _clip((v - 5.0) / 5.0, 0.0, 1.0)
        r_kin = vx_n * math.tan(steer) / L
        r_n = b * r_n + (1.0 - b) * r_kin
        vy_n = b * vy_n + (1.0 - b) * r_kin * lr
        psi_n = st[_PSI] + r_n * dt
        c = math.cos(psi_n)
        s_ = math.sin(psi_n)
        st[_X] += (vx_n * c - vy_n * s_) * dt
        st[_Y] += (vx_n * s_ + vy_n * c) * dt
        st[_PSI] = psi_n
        st[_VX] = vx_n
        st[_VY] = vy_n
        st[_R] = r_n
        st[_AXP] = 0.8 * st[_AXP] + 0.2 * (fx / m)
        st[_AYP] = 0.8 * st[_AYP] + 0.2 * (fy / m)
        # ---- tyres
        vxp = vx if vx > 0.0 else 0.0
        slide_f = abs(fyf) * v * abs(math.sin(af)) + abs(fxf) * vxp * 0.015 + exc_f * vxp * 0.5
        slide_r = abs(fyr) * v * abs(math.sin(ar)) + abs(fxr) * vxp * 0.015 + exc_r * vxp * 0.5
        st[_SLF] = slide_f
        st[_SLR] = slide_r
        heat_mult = 1.0 + (p[_CDO] - 1.0) * (1.0 - wet)
        cool_k = p[_CB] + p[_CSP] * v
        hf = p[_HGAIN] * slide_f * heat_mult + p[_RH] * 1e-3 * fzf * v - cool_k * (st[_TF] - track_temp)
        # rear tyres are wider: more surface to cool and more rubber to heat
        hr = p[_HGAIN] * slide_r * heat_mult + p[_RH] * 1e-3 * fzr * v - 1.3 * cool_k * (st[_TR] - track_temp)
        hot = p[_CTOPT] + p[_CTW]
        ovf = 1.0 + _clip((st[_TF] - hot) / 15.0, 0.0, 1.0)
        ovr = 1.0 + _clip((st[_TR] - hot) / 15.0, 0.0, 1.0)
        st[_TF] += hf / p[_HCAP] * dt
        st[_TR] += hr / (1.15 * p[_HCAP]) * dt
        wk = p[_CWR] * p[_WMULT] * dt * 1e-6
        st[_WF] += wk * slide_f * ovf
        st[_WR] += wk * slide_r * ovr
        if st[_GHOST] > 0.0:
            st[_GHOST] -= dt
            if st[_GHOST] < 0.0:
                st[_GHOST] = 0.0
        # ---- track position
        hint = int(st[_IDX])
        j, s_new, lat, hw = project_point(st[_X], st[_Y], hint, 4, 6, pts, seg_unit, seg_len, seg_normal,
                                         cum_s, half_w, n_pts)
        ds = s_new - st[_S]
        if ds < -0.5 * length:
            ds += length
        elif ds > 0.5 * length:
            ds -= length
        p_old = st[_PROG]
        p_new = p_old + ds
        st[_IDX] = j
        st[_S] = s_new
        st[_LAT] = lat
        st[_HW] = hw
        st[_PROG] = p_new
        if p_new > st[_MAXP]:
            st[_MAXP] = p_new
        k_old = math.floor(p_old / length)
        k_new = math.floor(p_new / length)
        if k_new > k_old:
            span = p_new - p_old
            frac = (k_new * length - p_old) / span if span > 0.0 else 1.0
            st[_CROSS] = t_now - dt + frac * dt
            flags[i] |= F_LAP
        # ---- walls: four body corners
        if check_walls:
            hl = 0.5 * p[_LEN]
            hwid = 0.5 * p[_WID]
            for a in range(4):
                fx_ = 1.0 if a < 2 else -1.0
                fy_ = 1.0 if (a % 2) == 0 else -1.0
                cx = st[_X] + fx_ * hl * c - fy_ * hwid * s_
                cy = st[_Y] + fx_ * hl * s_ + fy_ * hwid * c
                _, _, clat, chw = project_point(cx, cy, j, 6, 6, pts, seg_unit, seg_len, seg_normal,
                                                cum_s, half_w, n_pts)
                if abs(clat) > chw:
                    flags[i] |= F_WALL
                    break
        if st[_WF] >= 1.0 or st[_WR] >= 1.0:
            flags[i] |= F_TYRE
        # ---- stalls and wrong way
        vnow = math.sqrt(vx_n * vx_n + vy_n * vy_n)
        if vnow < stall_speed:
            st[_STALL] += dt
            flags[i] |= F_STALL
        else:
            st[_STALL] = 0.0
        herr = psi_n - heading[j]
        herr = (herr + math.pi) % (2.0 * math.pi) - math.pi
        if abs(herr) > wrong_angle:
            st[_WRONG] += dt
            flags[i] |= F_WRONG
        else:
            st[_WRONG] = 0.0


@njit(cache=True)
def near_pairs(S, active, radius, out):
    """Fill ``out`` (k, 2) with candidate colliding pairs; returns the count."""
    n = S.shape[0]
    k = 0
    r2 = radius * radius
    for a in range(n):
        if not active[a] or S[a, _GHOST] > 0.0:
            continue
        for b in range(a + 1, n):
            if not active[b] or S[b, _GHOST] > 0.0:
                continue
            dx = S[a, _X] - S[b, _X]
            dy = S[a, _Y] - S[b, _Y]
            if dx * dx + dy * dy < r2 and k < out.shape[0]:
                out[k, 0] = a
                out[k, 1] = b
                k += 1
    return k


@njit(cache=True)
def project_many(xs, ys, hints, back, fwd, pts, seg_unit, seg_len, seg_normal, cum_s, half_w, n_pts):
    m = xs.shape[0]
    idx = np.empty(m, dtype=np.int64)
    s = np.empty(m)
    lat = np.empty(m)
    hw = np.empty(m)
    for i in range(m):
        j, ss, la, h = project_point(xs[i], ys[i], hints[i], back, fwd, pts, seg_unit, seg_len,
                                     seg_normal, cum_s, half_w, n_pts)
        idx[i] = j
        s[i] = ss
        lat[i] = la
        hw[i] = h
    return idx, s, lat, hw
