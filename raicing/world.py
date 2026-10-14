"""The simulation world.

Per-car physics runs in JIT-compiled kernels (``raicing.physics.kernels``) on packed state
arrays; this class handles everything event-driven: laps, pits, contacts and the stewards,
eliminations, classification and replay recording.

Physics runs at a fixed ``dt`` (1/100 s). Drivers decide every ``decision_every`` steps
(20 Hz). The world is deterministic for a given track, weather, car specs and actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .config import COMPOUND_INDEX, COMPOUNDS, Regulations
from .physics import kernels as K
from .physics.car import SPEC_FIELDS, CarSpec, build_spec
from .track.geometry import Track
from .weather import WeatherScript, WetnessMap

RACING, PIT, FINISHED, DNF, DSQ = 0, 1, 2, 3, 4
STATUS_NAMES = {RACING: "racing", PIT: "pit", FINISHED: "finished", DNF: "dnf", DSQ: "dsq"}

ACT_DIM = 11
A_STEER, A_PEDAL, A_PIT = 0, 1, 2
A_COMPOUND = slice(3, 7)
A_MSG = slice(7, 11)
MSG_DIM = 4

_CIRCLE_OFFSETS = np.array([-1.6, 0.0, 1.6])
_CIRCLE_RADIUS = 1.0
_COMPOUND_PARAMS = ("grip", "t_opt", "t_width", "wear_rate", "wear_shape", "wet_loss", "dry_overheat")


@dataclass
class Incident:
    t: float
    a: int
    b: int
    kind: str            # "rear" | "side" | "brake_check"
    fault: int | None    # car index or None (racing incident)
    impulse: float
    closing: float = 0.0     # max closing speed during the contact (m/s)
    penalised: bool = False


@dataclass
class SessionConfig:
    kind: str = "race"            # "race" | "quali" | "lab"
    laps: int = 12
    interactions: bool = True     # car/car collisions and aerodynamic wake
    record: bool = False
    record_every: int = 10        # physics steps between replay frames
    max_time: float | None = None


def _state_property(name: str):
    col = K.SC[name]

    def getter(self):
        return self.S[:, col]

    def setter(self, value):
        self.S[:, col] = value
    return property(getter, setter)


def _param_property(name: str):
    col = K.PC[name]

    def getter(self):
        return self.PRM[:, col]
    return property(getter)


class World:
    # named views on the packed state
    x = _state_property("x")
    y = _state_property("y")
    psi = _state_property("psi")
    vx = _state_property("vx")
    vy = _state_property("vy")
    r = _state_property("r")
    steer = _state_property("steer")
    pedal = _state_property("pedal")
    cmd_steer = _state_property("cmd_steer")
    cmd_pedal = _state_property("cmd_pedal")
    ax_prev = _state_property("ax_prev")
    ay_prev = _state_property("ay_prev")
    temp_f = _state_property("temp_f")
    temp_r = _state_property("temp_r")
    wear_f = _state_property("wear_f")
    wear_r = _state_property("wear_r")
    damage = _state_property("damage")
    s = _state_property("s")
    lat = _state_property("lat")
    hw = _state_property("hw")
    progress = _state_property("progress")
    max_progress = _state_property("max_progress")
    ghost = _state_property("ghost")
    stall_timer = _state_property("stall_timer")
    wrong_timer = _state_property("wrong_timer")
    wet_at_car = _state_property("wet_at_car")
    slide_f = _state_property("slide_f")
    slide_r = _state_property("slide_r")
    c_grip = _param_property("c_grip")
    c_t_opt = _param_property("c_t_opt")
    c_t_width = _param_property("c_t_width")
    c_wear_rate = _param_property("c_wear_rate")
    c_wear_shape = _param_property("c_wear_shape")
    c_wet_loss = _param_property("c_wet_loss")
    c_dry_overheat = _param_property("c_dry_overheat")

    @property
    def idx(self) -> np.ndarray:
        return self.S[:, K.SC["idx"]].astype(np.int64)

    def __init__(self, track: Track, specs: list[CarSpec], regs: Regulations | None = None,
                 weather: WeatherScript | None = None, session: SessionConfig | None = None,
                 teams: list[int] | None = None, seed: int = 0):
        self.track = track
        self.regs = regs or Regulations()
        self.session = session or SessionConfig()
        self.weather = weather or WeatherScript.dry()
        self.seed = seed
        rr = self.regs.race
        self.dt = rr.physics_dt
        self.decision_every = rr.decision_every
        self.n = n = len(specs)
        self.specs = list(specs)
        self.S = np.zeros((n, K.NS), dtype=np.float64)
        self.PRM = np.zeros((n, K.NP_), dtype=np.float64)
        for i, sp in enumerate(specs):
            for f in SPEC_FIELDS:
                self.PRM[i, K.PC[f]] = getattr(sp, f)
            self.PRM[i, K.PC["wheelbase"]] = sp.lf + sp.lr
        self.p = {name: self.PRM[:, col] for name, col in K.PC.items()}
        self._tyre_window = np.array([getattr(sp, "tyre_window", 1.0) for sp in specs], dtype=np.float64)
        self.S[:, K.SC["drag_mult"]] = 1.0
        self.S[:, K.SC["df_mult"]] = 1.0
        self.temp_f = rr.start_tyre_temp
        self.temp_r = rr.start_tyre_temp
        self.teams = np.array(teams if teams is not None else list(range(n)), dtype=np.int64)
        self.teammate = np.full(n, -1, dtype=np.int64)
        for i in range(n):
            for j in range(n):
                if i != j and self.teams[i] == self.teams[j]:
                    self.teammate[i] = j
        comp = self.regs.compounds
        self._ctab = {k: np.array([getattr(comp[c], k) for c in COMPOUNDS]) for k in _COMPOUND_PARAMS}
        self.compound = np.full(n, COMPOUND_INDEX["medium"], dtype=np.int64)
        self.status = np.full(n, RACING, dtype=np.int64)
        self.reason: list[str] = [""] * n
        self.pit_request = np.zeros(n, dtype=bool)
        self.pit_choice = np.full(n, COMPOUND_INDEX["medium"], dtype=np.int64)
        self.pit_timer = np.zeros(n)
        self.pit_total = np.zeros(n)
        self.pit_p0 = np.zeros(n)
        self.pit_p1 = np.zeros(n)
        self.pit_stops = np.zeros(n, dtype=np.int64)
        self.penalty_time = np.zeros(n)
        self.finish_time = np.full(n, np.nan)
        self.finish_order = np.full(n, -1, dtype=np.int64)
        self.end_time = np.full(n, np.nan)
        self.lap_times: list[list[float]] = [[] for _ in range(n)]
        self.lap_start = np.full(n, np.nan)
        self.messages = np.zeros((n, MSG_DIM), dtype=np.float64)
        self.last_brakecheck = np.full(n, -1e9)
        self.position = np.arange(n, dtype=np.int64)
        self.flags = np.zeros(n, dtype=np.int64)
        self._pairs = np.zeros((max(n * (n - 1) // 2, 1), 2), dtype=np.int64)
        self.t = 0.0
        self.steps = 0
        self.flag_time: float | None = None
        self.grace_deadline: float | None = None
        self._finish_counter = 0
        self.events: list[dict[str, Any]] = []
        self.incidents: list[Incident] = []
        self._contact_last: dict[tuple[int, int], float] = {}
        self._pending: list[tuple[int, int, float, int]] = []
        self.team_penalties: dict[int, int] = {}
        self._pit_new: dict[int, tuple[int, bool]] = {}
        self.frames: list[list] = []
        self.weather_frames: list[list] = []
        self.wetmap = WetnessMap(track.n_wet_cells(self.regs.track.wet_cell), self.regs.track.wet_cell,
                                 track.slow_drying, self.regs.weather, self.weather.initial_wetness, seed)
        self.rain = self.weather.intensity(0.0)
        self.track_temp = self._track_temp()
        self._refresh_compounds()
        tr = track
        self._trk = (tr.points, tr.seg_unit, tr.seg_len, tr.seg_normal, tr.s, tr.half_width, tr.heading,
                     tr.n, tr.length)
        # stewards' reference speed profile (base car) for the brake-check test
        prof = _cached_profile(track, self.regs)
        win = max(1, int(150.0 / track.ds))
        ext = np.concatenate([prof, prof[:win]])
        from numpy.lib.stride_tricks import sliding_window_view
        self.vmin_ahead = sliding_window_view(ext, win)[: track.n].min(axis=1)
        self.speed_profile = prof

    # ------------------------------------------------------------------ helpers
    def _track_temp(self) -> float:
        return self.weather.ambient + 8.0 * (1.0 - min(self.rain * 2.0, 1.0))

    def _refresh_compounds(self) -> None:
        for k, tab in self._ctab.items():
            self.PRM[:, K.PC["c_" + k]] = tab[self.compound]
        self.PRM[:, K.PC["c_t_width"]] *= self._tyre_window

    def project(self, xy: np.ndarray, hint: np.ndarray, back: int = 6, fwd: int = 8):
        pts, su, sl, sn, cs, hw, _, n, _ = self._trk
        return K.project_many(np.ascontiguousarray(xy[:, 0], dtype=np.float64),
                              np.ascontiguousarray(xy[:, 1], dtype=np.float64),
                              np.asarray(hint, dtype=np.int64), back, fwd, pts, su, sl, sn, cs, hw, n)

    def place_car(self, i: int, s: float, lat: float = 0.0, speed: float = 0.0, heading_offset: float = 0.0,
                  compound: str | int = "medium", tyre_temp: float | None = None, wear: float = 0.0,
                  progress: float | None = None) -> None:
        tr = self.track
        pos = tr.point_at(np.array([s]), np.array([lat]))[0]
        hd = float(tr.heading_at(s)) + heading_offset
        row = self.S[i]
        row[:] = 0.0
        row[K.SC["drag_mult"]] = 1.0
        row[K.SC["df_mult"]] = 1.0
        row[K.SC["x"]], row[K.SC["y"]], row[K.SC["psi"]] = pos[0], pos[1], hd
        row[K.SC["vx"]] = speed
        t0 = self.regs.race.start_tyre_temp if tyre_temp is None else tyre_temp
        row[K.SC["temp_f"]] = row[K.SC["temp_r"]] = t0
        row[K.SC["wear_f"]] = row[K.SC["wear_r"]] = wear
        self.compound[i] = compound if isinstance(compound, (int, np.integer)) else COMPOUND_INDEX[compound]
        self.status[i] = RACING
        self.reason[i] = ""
        hint = tr.index_at(np.array([s])).astype(np.int64)
        idx, ss, la, hw = self.project(pos[None, :], hint)
        row[K.SC["idx"]] = idx[0]
        row[K.SC["s"]] = ss[0]
        row[K.SC["lat"]] = la[0]
        row[K.SC["hw"]] = hw[0]
        prog = ss[0] if progress is None else progress
        row[K.SC["progress"]] = prog
        row[K.SC["max_progress"]] = prog
        row[K.SC["wet_at_car"]] = self.wetmap.at(np.array([ss[0]]), tr.length)[0]
        self.pit_request[i] = False
        self.lap_start[i] = np.nan
        self._refresh_compounds()

    def setup_grid(self, order: list[int], compounds: list[str] | None = None) -> None:
        rr = self.regs.race
        for pos, i in enumerate(order):
            back = 10.0 + pos * rr.grid_row_gap
            lat = rr.grid_lateral * (1 if pos % 2 == 0 else -1)
            hw = float(self.track.width_at(self.track.length - back))
            lat = float(np.clip(lat, -hw + 2.5, hw - 2.5))
            comp = compounds[i] if compounds else "medium"
            self.place_car(i, self.track.length - back, lat, 0.0, 0.0, comp, progress=-back)
        self._update_positions()

    def setup_quali(self, compounds: list[str] | None = None) -> None:
        rr = self.regs.race
        L = self.track.length
        for i in range(self.n):
            comp = compounds[i] if compounds else "soft"
            hw = float(self.track.width_at(rr.pit_exit_after_line))
            self.place_car(i, rr.pit_exit_after_line, -0.4 * hw, rr.pit_exit_speed, 0.0, comp,
                           tyre_temp=rr.pit_tyre_temp, progress=rr.pit_exit_after_line - L)

    # ------------------------------------------------------------------ actions
    def apply_actions(self, actions: np.ndarray) -> None:
        a = np.asarray(actions, dtype=np.float64)
        if a.ndim == 1:
            a = a[None, :]
        a = np.nan_to_num(a, nan=0.0, posinf=1.0, neginf=-1.0)
        k = a.shape[1]
        act = self.status == RACING
        steer = np.clip(a[:, A_STEER], -1.0, 1.0)
        pedal = np.clip(a[:, A_PEDAL], -1.0, 1.0)
        self.cmd_steer = np.where(act, steer, self.cmd_steer)
        self.cmd_pedal = np.where(act, pedal, self.cmd_pedal)
        if k > A_PIT:
            self.pit_request = np.where(act, a[:, A_PIT] > 0.0, self.pit_request)
        if k >= 7:
            choice = np.argmax(a[:, A_COMPOUND], axis=1)
            self.pit_choice = np.where(act, choice, self.pit_choice)
        if k >= 11:
            msg = np.clip(a[:, A_MSG], -1.0, 1.0)
            self.messages = np.where(act[:, None], msg, self.messages)
        if self.session.kind == "race":
            v = np.hypot(self.vx, self.vy)
            vref = self.vmin_ahead[self.idx]
            hard = act & (pedal < -0.5) & (v < self.regs.race.brake_check_speed_ratio * vref)
            self.last_brakecheck = np.where(hard, self.t, self.last_brakecheck)

    def step(self, actions: np.ndarray | None = None) -> None:
        """Advance one decision period (``decision_every`` physics steps)."""
        if actions is not None:
            self.apply_actions(actions)
        for _ in range(self.decision_every):
            self._physics_step()
        self._post_decision()

    # ------------------------------------------------------------------ physics
    def _physics_step(self) -> None:
        dt = self.dt
        self.t += dt
        self.steps += 1
        if (self.status == PIT).any():
            self._pit_step(self.status == PIT)
        act = self.status == RACING
        if act.any():
            inter = self.session.interactions
            if inter:
                ar = self.regs.aero
                K.aero_factors(self.S, act, ar.slipstream_range, ar.slipstream_drag_cut, ar.dirty_air_range,
                               ar.dirty_air_downforce_cut, ar.wake_half_width, self.track.length)
            pts, su, sl, sn, cs, hw, hd, n, L = self._trk
            rr = self.regs.race
            K.step_cars(self.S, self.PRM, act, self.flags, dt, self.t, self.track_temp,
                        pts, su, sl, sn, cs, hw, hd, n, L, rr.wall_contact_eliminates,
                        rr.stall_speed, rr.wrong_way_angle)
            if self.flags.any():
                self._handle_flags()
            if inter:
                self._collisions(self.status == RACING)
        if self._pending:
            self._pending = [p for p in self._pending if p[2] >= self.t]
        if self.session.record and self.steps % self.session.record_every == 0:
            self._record_frame()

    def _handle_flags(self) -> None:
        f = self.flags
        rr = self.regs.race
        for i in np.nonzero(f & K.F_LAP)[0]:
            self._on_lap_cross(int(i), float(self.S[i, K.SC["cross_time"]]))
        for i in np.nonzero(f & K.F_WALL)[0]:
            self._eliminate(int(i), "wall")
        for i in np.nonzero(f & K.F_TYRE)[0]:
            self._eliminate(int(i), "tyre")
        if self.session.kind != "lab":
            for i in np.nonzero((f & K.F_STALL) & (self.stall_timer > rr.stall_time))[0]:
                self._eliminate(int(i), "stalled")
            for i in np.nonzero((f & K.F_WRONG) & (self.wrong_timer > rr.wrong_way_time))[0]:
                self._eliminate(int(i), "wrong_way")

    # ------------------------------------------------------------------ laps / finish
    def _on_lap_cross(self, i: int, t_cross: float) -> None:
        if not np.isnan(self.lap_start[i]):
            self.lap_times[i].append(t_cross - self.lap_start[i])
        self.lap_start[i] = t_cross
        laps_done = int(np.floor(self.progress[i] / self.track.length + 1e-9))
        kind = self.session.kind
        if kind == "race":
            if laps_done >= 1 and (laps_done >= self.session.laps or self.flag_time is not None):
                self._finish(i, t_cross)
        elif kind == "quali":
            if laps_done >= 1:
                self._finish(i, t_cross)

    def _finish(self, i: int, t_cross: float, timed: bool = True) -> None:
        if self.status[i] in (FINISHED, DNF, DSQ):
            return
        self.status[i] = FINISHED
        self.finish_time[i] = t_cross if timed else np.nan
        self.end_time[i] = t_cross
        self.finish_order[i] = self._finish_counter
        self._finish_counter += 1
        self.vx[i] = self.vy[i] = self.r[i] = 0.0
        if self.flag_time is None and self.session.kind == "race" and timed:
            self.flag_time = t_cross
            laps = max(len(self.lap_times[i]), 1)
            avg = t_cross / (laps + 0.5)
            self.grace_deadline = t_cross + self.regs.race.finish_grace_factor * avg
        self.events.append({"t": round(t_cross, 3), "type": "finish", "car": i, "timed": timed})

    # ------------------------------------------------------------------ pits
    def _enter_pit(self, i: int) -> None:
        rr = self.regs.race
        total = rr.pit_lane_time + rr.pit_service_time
        repair = bool(self.damage[i] > rr.pit_repair_threshold)
        if repair:
            total += rr.pit_repair_time
        self.status[i] = PIT
        self.pit_timer[i] = total
        self.pit_total[i] = total
        self.pit_p0[i] = self.progress[i]
        L = self.track.length
        lap_base = np.floor((self.progress[i] + rr.pit_entry_before_line + 1.0) / L) * L
        self.pit_p1[i] = lap_base + rr.pit_exit_after_line
        self.pit_stops[i] += 1
        self.pit_request[i] = False
        new_comp = int(self.pit_choice[i])
        self.events.append({"t": round(self.t, 3), "type": "pit_in", "car": i,
                            "compound": COMPOUNDS[new_comp], "repair": repair})
        self._pit_new[i] = (new_comp, repair)
        self.vx[i] = self.vy[i] = self.r[i] = 0.0

    def _pit_step(self, pit: np.ndarray) -> None:
        dt = self.dt
        rr = self.regs.race
        L = self.track.length
        for i in np.nonzero(pit)[0]:
            i = int(i)
            self.pit_timer[i] -= dt
            frac = 1.0 - max(self.pit_timer[i], 0.0) / self.pit_total[i]
            p_old = float(self.progress[i])
            p_new = self.pit_p0[i] + frac * (self.pit_p1[i] - self.pit_p0[i])
            self.progress[i] = p_new
            if np.floor(p_new / L) > np.floor(p_old / L):
                span = p_new - p_old
                t_cross = self.t - dt + ((np.floor(p_new / L) * L - p_old) / span if span > 0 else 1.0) * dt
                self._on_lap_cross(i, t_cross)
            if self.status[i] != PIT:
                continue  # took the chequered flag in the pit lane
            if self.pit_timer[i] <= 0.0:
                comp, repair = self._pit_new.pop(i, (int(self.compound[i]), False))
                s = rr.pit_exit_after_line
                hw = float(self.track.width_at(s))
                laps = list(self.lap_times[i])
                ls = self.lap_start[i]
                dmg = 0.0 if repair else float(self.damage[i])
                self.place_car(i, s, -0.45 * hw, rr.pit_exit_speed, 0.0, comp, tyre_temp=rr.pit_tyre_temp,
                               progress=self.pit_p1[i])
                self.lap_times[i] = laps
                self.lap_start[i] = ls
                self.damage[i] = dmg
                self.ghost[i] = rr.pit_ghost_time
                self.events.append({"t": round(self.t, 3), "type": "pit_out", "car": i, "compound": COMPOUNDS[comp]})

    # ------------------------------------------------------------------ contacts
    def _collisions(self, act: np.ndarray) -> None:
        k = K.near_pairs(self.S, act, 5.4, self._pairs)
        for m in range(k):
            a, b = int(self._pairs[m, 0]), int(self._pairs[m, 1])
            if self.status[a] == RACING and self.status[b] == RACING:
                self._resolve_pair(a, b)

    def _circles(self, i: int) -> np.ndarray:
        c, s = np.cos(self.psi[i]), np.sin(self.psi[i])
        return np.stack([self.x[i] + _CIRCLE_OFFSETS * c, self.y[i] + _CIRCLE_OFFSETS * s], axis=1)

    def _world_vel(self, i: int) -> np.ndarray:
        c, s = np.cos(self.psi[i]), np.sin(self.psi[i])
        return np.array([self.vx[i] * c - self.vy[i] * s, self.vx[i] * s + self.vy[i] * c])

    def _resolve_pair(self, a: int, b: int) -> None:
        ca, cb = self._circles(a), self._circles(b)
        d = ca[:, None, :] - cb[None, :, :]
        dist = np.hypot(d[..., 0], d[..., 1])
        k = np.unravel_index(np.argmin(dist), dist.shape)
        dmin = dist[k]
        if dmin >= 2 * _CIRCLE_RADIUS:
            return
        nvec = d[k] / max(dmin, 1e-6)          # from b to a
        pen = 2 * _CIRCLE_RADIUS - dmin
        contact = 0.5 * (ca[k[0]] + cb[k[1]])
        P = self.p
        ma, mb = P["mass"][a], P["mass"][b]
        ia, ib = P["iz"][a], P["iz"][b]
        va, vb = self._world_vel(a), self._world_vel(b)
        ra = contact - np.array([self.x[a], self.y[a]])
        rb = contact - np.array([self.x[b], self.y[b]])
        vpa = va + self.r[a] * np.array([-ra[1], ra[0]])
        vpb = vb + self.r[b] * np.array([-rb[1], rb[0]])
        vrel = float(np.dot(vpa - vpb, nvec))
        tot = ma + mb
        self.x[a] += nvec[0] * pen * mb / tot
        self.y[a] += nvec[1] * pen * mb / tot
        self.x[b] -= nvec[0] * pen * ma / tot
        self.y[b] -= nvec[1] * pen * ma / tot
        j = 0.0
        if vrel < 0.0:
            e = self.regs.race.collision_restitution
            rna = ra[0] * nvec[1] - ra[1] * nvec[0]
            rnb = rb[0] * nvec[1] - rb[1] * nvec[0]
            j = -(1 + e) * vrel / (1 / ma + 1 / mb + rna * rna / ia + rnb * rnb / ib)
            va = va + j * nvec / ma
            vb = vb - j * nvec / mb
            self.r[a] += rna * j / ia
            self.r[b] -= rnb * j / ib
            for i, vw in ((a, va), (b, vb)):
                c, s = np.cos(self.psi[i]), np.sin(self.psi[i])
                self.vx[i] = max(vw[0] * c + vw[1] * s, 0.0)
                self.vy[i] = -vw[0] * s + vw[1] * c
            closing = -vrel
            dmg = self.regs.race.damage_per_speed2 * max(closing - 1.5, 0.0) ** 2
            self.damage[a] += dmg
            self.damage[b] += dmg
        closing = max(-vrel, 0.0)
        key = (a, b)
        last = self._contact_last.get(key, -1e9)
        self._contact_last[key] = self.t
        if self.t - last > 0.5:
            self._new_incident(a, b, j, closing)
        elif j > 0:
            for inc in reversed(self.incidents):
                if (inc.a, inc.b) == key:
                    inc.impulse += j
                    inc.closing = max(inc.closing, closing)
                    self._maybe_penalise(inc)
                    break
        for i in (a, b):
            if self.damage[i] >= 1.0 and self.status[i] == RACING:
                self._eliminate(i, "broken")

    def _new_incident(self, a: int, b: int, impulse: float, closing: float = 0.0) -> None:
        rr = self.regs.race
        L = self.track.length
        d = (self.s[b] - self.s[a] + 0.5 * L) % L - 0.5 * L   # >0: b ahead of a
        if abs(d) > rr.rear_end_gap:
            rear, front = (a, b) if d > 0 else (b, a)
            kind = "rear"
            fault: int | None = rear
            if self.t - self.last_brakecheck[front] <= rr.brake_check_window:
                fault = front
                kind = "brake_check"
        else:
            kind = "side"
            idx = self.idx
            side = float(np.sign(self.lat[b] - self.lat[a])) or 1.0
            toward_a = float(np.dot(self._world_vel(a), self.track.normal[idx[a]])) * side
            toward_b = -float(np.dot(self._world_vel(b), self.track.normal[idx[b]])) * side
            if toward_a - toward_b > rr.side_contact_lat_margin:
                fault = a
            elif toward_b - toward_a > rr.side_contact_lat_margin:
                fault = b
            else:
                fault = None
        inc = Incident(t=self.t, a=a, b=b, kind=kind, fault=fault, impulse=impulse, closing=closing)
        self.incidents.append(inc)
        self.events.append({"t": round(self.t, 3), "type": "contact", "cars": [a, b], "kind": kind,
                            "fault": fault})
        if fault is not None and self.session.kind == "race":
            victim = b if fault == a else a
            self._pending.append((fault, victim, self.t + rr.elimination_window, len(self.incidents) - 1))
            self._maybe_penalise(inc)

    def _maybe_penalise(self, inc: Incident) -> None:
        if (inc.fault is None or inc.penalised or self.session.kind != "race"
                or inc.closing < self.regs.race.penalty_min_closing):
            return
        inc.penalised = True
        self.penalty_time[inc.fault] += self.regs.race.contact_time_penalty
        self.events.append({"t": round(self.t, 3), "type": "penalty", "car": inc.fault,
                            "seconds": self.regs.race.contact_time_penalty})

    # ------------------------------------------------------------------ eliminations
    def _eliminate(self, i: int, reason: str, status: int = DNF) -> None:
        if self.status[i] in (FINISHED, DNF, DSQ):
            if status == DSQ and self.status[i] != DSQ:
                self.status[i] = DSQ
                self.reason[i] = reason
                self.events.append({"t": round(self.t, 3), "type": "dsq", "car": i, "reason": reason})
            return
        self.status[i] = status
        self.reason[i] = reason
        self.end_time[i] = self.t
        self.vx[i] = self.vy[i] = self.r[i] = 0.0
        self.events.append({"t": round(self.t, 3), "type": "dsq" if status == DSQ else "out", "car": i,
                            "reason": reason})
        if status == DNF and self.session.kind == "race":
            for fault, victim, deadline, _ in list(self._pending):
                if victim == i and deadline >= self.t:
                    self._disqualify(fault, victim)

    def _disqualify(self, fault: int, victim: int) -> None:
        team = int(self.teams[fault])
        pts = self.regs.race.team_penalty_points
        self.team_penalties[team] = self.team_penalties.get(team, 0) + pts
        self._pending = [p for p in self._pending if p[0] != fault]
        self.events.append({"t": round(self.t, 3), "type": "team_penalty", "team": team,
                            "points": pts, "car": fault, "victim": victim})
        self._eliminate(fault, "caused_elimination", DSQ)

    # ------------------------------------------------------------------ bookkeeping
    def _post_decision(self) -> None:
        dt_dec = self.dt * self.decision_every
        self.rain = self.weather.intensity(self.t)
        self.wetmap.update(self.rain, dt_dec)
        self.track_temp = self._track_temp()
        self.wet_at_car = self.wetmap.at(self.s, self.track.length)
        # pit entry: request active and the car is in the entry zone just before the line
        if self.session.kind == "race" and self.pit_request.any():
            rr = self.regs.race
            L = self.track.length
            pe = L - rr.pit_entry_before_line
            zone = (self.status == RACING) & self.pit_request & (self.s >= pe) & (self.s < L - 2.0)
            for i in np.nonzero(zone)[0]:
                self._enter_pit(int(i))
        self._update_positions()
        if self.session.kind == "race" and self.grace_deadline is not None and self.t > self.grace_deadline:
            for i in np.nonzero((self.status == RACING) | (self.status == PIT))[0]:
                self._finish(int(i), self.t, timed=False)

    def _update_positions(self) -> None:
        L = self.track.length
        st = self.status
        laps = np.floor(self.progress / L + 1e-9)
        timed = (st == FINISHED) & ~np.isnan(self.finish_time)
        ft = np.where(np.isnan(self.finish_time), 0.0, self.finish_time) + self.penalty_time
        key = np.where(timed, -4e12 - laps * 1e6 + ft, 0.0)
        running = (st == RACING) | (st == PIT) | ((st == FINISHED) & ~timed)
        key = np.where(running, -2e12 - self.progress, key)
        key = np.where(st == DNF, -self.max_progress, key)
        key = np.where(st == DSQ, 1e12 - self.max_progress, key)
        order = np.argsort(key, kind="stable")
        pos = np.empty(self.n, dtype=np.int64)
        pos[order] = np.arange(self.n)
        self.position = pos

    def done(self) -> bool:
        running = np.isin(self.status, (RACING, PIT))
        if not running.any():
            return True
        limit = self.session.max_time
        return limit is not None and self.t >= limit

    # ------------------------------------------------------------------ replay
    def _record_frame(self) -> None:
        v = np.hypot(self.vx, self.vy)
        L = self.track.length
        frame: list[Any] = [round(self.t, 2)]
        S = self.S
        for i in range(self.n):
            frame.append([
                round(float(S[i, 0]), 1), round(float(S[i, 1]), 1), round(float(S[i, 2]), 2),
                round(float(v[i]), 1), int(self.status[i]), int(self.compound[i]),
                round(float(max(self.wear_f[i], self.wear_r[i])), 3),
                round(float(0.5 * (self.temp_f[i] + self.temp_r[i]))),
                int(np.floor(self.progress[i] / L)), int(self.position[i]),
                round(float(self.damage[i]), 2), round(float(self.steer[i]), 2), round(float(self.pedal[i]), 2),
            ])
        self.frames.append(frame)
        if self.steps % (self.session.record_every * 10) == 0:
            self.weather_frames.append([round(self.t, 1), round(self.rain, 3),
                                        np.round(self.wetmap.wet, 2).tolist()])

    def car_state(self, i: int) -> dict[str, Any]:
        return {
            "x": float(self.x[i]), "y": float(self.y[i]), "psi": float(self.psi[i]),
            "speed": float(np.hypot(self.vx[i], self.vy[i])), "status": STATUS_NAMES[int(self.status[i])],
            "compound": COMPOUNDS[int(self.compound[i])], "wear": float(max(self.wear_f[i], self.wear_r[i])),
            "temp_f": float(self.temp_f[i]), "temp_r": float(self.temp_r[i]), "damage": float(self.damage[i]),
            "progress": float(self.progress[i]), "lap_times": list(self.lap_times[i]),
        }


_PROFILE_CACHE: dict[tuple, np.ndarray] = {}


def _cached_profile(track: Track, regs: Regulations) -> np.ndarray:
    key = (track.seed, round(track.length, 3), regs.version, regs.car.mu_peak, regs.car.cla, regs.car.power)
    prof = _PROFILE_CACHE.get(key)
    if prof is None:
        base = build_spec(regs)
        prof = track.speed_profile(base.mu, base.mass, base.cla, base.power, base.cda)
        if len(_PROFILE_CACHE) > 64:
            _PROFILE_CACHE.clear()
        _PROFILE_CACHE[key] = prof
    return prof
