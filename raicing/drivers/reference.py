"""Scripted reference drivers.

They are *system* drivers used for balancing, filler teams, imitation learning and as the
yardstick of the Driver Rating. Team pilots must be neural brains; these scripts read the
world state directly and are never allowed in a team entry.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import COMPOUND_INDEX
from ..track.geometry import wrap_angle
from ..world import ACT_DIM, RACING, World
from .racing_line import line_curvature, racing_line, speed_profile_from_curvature


@dataclass(frozen=True)
class Personality:
    key: str
    label: str
    grip_use: float        # share of the estimated grip used by the speed profile
    brake_use: float       # share of the braking capacity assumed
    lookahead: float       # steering look-ahead gain
    pit_wear: float        # wear level that triggers a stop
    aggression: float      # willingness to attempt overtakes (0..1)
    smooth: float          # steering smoothing (0..1)
    cooperative: bool = False


PERSONALITIES: dict[str, Personality] = {
    "debutant": Personality("debutant", "Débutant", 0.80, 0.70, 1.25, 0.70, 0.10, 0.5),
    "rapide": Personality("rapide", "Rapide mais risqué", 0.975, 0.96, 0.90, 0.88, 0.90, 0.1),
    "prudent": Personality("prudent", "Prudent", 0.88, 0.80, 1.05, 0.62, 0.20, 0.35),
    "pneus": Personality("pneus", "Spécialiste des pneus", 0.92, 0.86, 1.00, 0.82, 0.40, 0.55),
    "cooperatif": Personality("cooperatif", "Coopératif", 0.93, 0.87, 1.00, 0.75, 0.50, 0.3, True),
    "pro": Personality("pro", "Référence", 0.96, 0.92, 0.95, 0.80, 0.60, 0.25),
}


class ReferenceDriver:
    """Drives a subset of cars of a world. Call ``act()`` once per decision."""

    def __init__(self, world: World, cars: list[int], personalities: list[str] | str = "pro",
                 laps: int | None = None):
        self.w = world
        self.cars = list(cars)
        if isinstance(personalities, str):
            personalities = [personalities] * len(self.cars)
        self.pers = [PERSONALITIES[p] for p in personalities]
        tr = world.track
        self.line = racing_line(tr)
        k = line_curvature(tr, self.line)
        self.line_k = k
        lp = tr.points + tr.normal * self.line[:, None]
        d = np.roll(lp, -1, axis=0) - np.roll(lp, 1, axis=0)
        self.line_heading = np.arctan2(d[:, 1], d[:, 0])
        self.laps = laps
        # speed profiles for a grid of effective friction levels; interpolated at run time
        self.mu_grid = np.array([0.55, 0.7, 0.85, 1.0, 1.15, 1.3, 1.45, 1.6, 1.8])
        self.profiles = []
        for i, p in zip(self.cars, self.pers):
            P = world.p
            grid = np.stack([speed_profile_from_curvature(
                k, tr.ds, mu=mu, mass=P["mass"][i], cla=P["cla"][i], power=P["power"][i], cda=P["cda"][i],
                brake_decel=9.81 * mu * p.brake_use * 0.9) for mu in self.mu_grid])
            self.profiles.append(grid)
        self.prev_steer = np.zeros(len(self.cars))
        self.pass_offset = np.zeros(len(self.cars))
        self.lat_target = np.full(len(self.cars), np.nan)

    def act(self) -> np.ndarray:
        w = self.w
        tr = w.track
        out = np.zeros((len(self.cars), ACT_DIM))
        L = tr.length
        for k, (i, p) in enumerate(zip(self.cars, self.pers)):
            if w.status[i] != RACING:
                continue
            v = float(np.hypot(w.vx[i], w.vy[i]))
            idx = int(w.idx[i])
            # ---- grip estimate (weather, tyres)
            comp = int(w.compound[i])
            wet = float(w.wet_at_car[i])
            wet_loss = float(w.c_wet_loss[i])
            base_grip = float(w.c_grip[i])
            grip_now = base_grip * (1.0 - wet_loss * wet)
            tfac = float(np.clip(1.0 - 0.25 * ((0.5 * (w.temp_f[i] + w.temp_r[i]) - w.c_t_opt[i]) / w.c_t_width[i]) ** 2, 0.7, 1.0))
            wear = float(max(w.wear_f[i], w.wear_r[i]))
            wfac = 1.0 - 0.28 * wear ** float(w.c_wear_shape[i])
            # effective friction: compound, weather, temperature, wear, load effects, personality
            mu_eff = float(w.p["mu"][i]) * grip_now * tfac * wfac * 0.92 * p.grip_use
            mu_eff = float(np.clip(mu_eff, self.mu_grid[0], self.mu_grid[-1]))
            # ---- speed target
            grid = self.profiles[k]
            j = int(np.clip(np.searchsorted(self.mu_grid, mu_eff) - 1, 0, len(self.mu_grid) - 2))
            f = (mu_eff - self.mu_grid[j]) / (self.mu_grid[j + 1] - self.mu_grid[j])
            ahead = max(int((v * 0.5 + 6.0) / tr.ds), 2)
            ids = (idx + np.arange(ahead)) % tr.n
            v_target = float(((1 - f) * grid[j, ids] + f * grid[j + 1, ids]).min())
            # ---- traffic: lateral corridor and speed limits from the surrounding cars
            lim = float(tr.half_width[idx]) - 2.9
            lo, hi = -lim, lim
            desired = float(self.line[idx])
            ds_all = (w.s - w.s[i] + 0.5 * L) % L - 0.5 * L
            my_lat = float(w.lat[i])
            for j in range(w.n if w.session.interactions else 0):
                if j == i or w.status[j] != RACING or w.ghost[j] > 0 or w.ghost[i] > 0:
                    continue
                d = float(ds_all[j])
                lj = float(w.lat[j])
                dl = lj - my_lat
                vj = float(np.hypot(w.vx[j], w.vy[j]))
                if -7.0 < d < 7.0 and abs(dl) < 5.5:
                    # alongside: keep to my side of the other car
                    if dl > 0:
                        hi = min(hi, lj - 3.6)
                    else:
                        lo = max(lo, lj + 3.6)
                    if d > 1.5 and abs(dl) < 2.8:
                        v_target = min(v_target, vj - 2.0)
                elif 0.0 < d < 70.0 and abs(dl) < 3.8:
                    closing = v - vj
                    room_left = tr.half_width[int(w.idx[j])] - lj
                    room_right = tr.half_width[int(w.idx[j])] + lj
                    if p.aggression > 0.25 and closing > 0.5 and max(room_left, room_right) > 6.5 and d < 40.0:
                        side = 1.0 if room_left > room_right else -1.0
                        desired = lj + side * 4.4
                    d_safe = 6.0 + 0.25 * v
                    # time-to-collision based following speed
                    v_follow = vj + 0.5 * (d - d_safe)
                    if abs(desired - lj) < 3.8 or d < d_safe:
                        v_target = min(v_target, max(v_follow, 0.0))
            if lo > hi:
                mid = 0.5 * (lo + hi)
                lo = hi = mid
                v_target *= 0.9
            self.pass_offset[k] = 0.85 * self.pass_offset[k] + 0.15 * (desired - float(self.line[idx]))
            lat_t = float(np.clip(self.line[idx] + self.pass_offset[k], lo, hi))
            if abs(lat_t) > lim:  # never trade the wall for a car
                lat_t = float(np.clip(lat_t, -lim, lim))
                v_target *= 0.92
            # smooth lateral target (no sudden lane changes)
            # (rate-limit the offset from the racing line, not the line itself)
            off = lat_t - float(self.line[idx])
            prev_off = self.lat_target[k] if np.isfinite(self.lat_target[k]) else my_lat - float(self.line[idx])
            off = prev_off + float(np.clip(off - prev_off, -0.12, 0.12))
            self.lat_target[k] = off
            lat_t = float(np.clip(float(self.line[idx]) + off, -lim, lim))
            # ---- steering: feed-forward curvature + Stanley correction on the racing line
            look = (idx + max(int(v * 0.12 / tr.ds), 1)) % tr.n
            e_lat = float(w.lat[i]) - lat_t
            chi = float(w.psi[i]) + float(np.arctan2(w.vy[i], max(w.vx[i], 1.0)))
            e_psi = float(wrap_angle(chi - self.line_heading[look]))
            wb = float(w.p["wheelbase"][i])
            kap = float(self.line_k[look])
            delta = np.arctan(wb * kap) * (1.0 + 0.00012 * v * v)
            k_lat = p.lookahead * (2.0 + 1.2 * float(np.clip((v - 25.0) / 30.0, 0.0, 1.0)))
            delta += -e_psi - np.arctan(k_lat * e_lat / (v + 6.0))
            delta += -0.12 * (float(w.r[i]) - v * kap)
            # never ask for more steering than the tyres can use at this speed
            mu_now = float(w.p["mu"][i]) * grip_now * tfac * wfac
            ay_cap = 9.81 * mu_now * (1.0 + 0.6 * v * v * float(w.p["cla"][i]) / (float(w.p["mass"][i]) * 9.81))
            d_cap = 1.15 * wb * ay_cap / max(v * v, 1.0) + 0.015
            delta = float(np.clip(delta, -d_cap, d_cap))
            cmd = float(np.clip(delta / w.p["max_steer"][i], -1.0, 1.0))
            cmd = p.smooth * self.prev_steer[k] + (1.0 - p.smooth) * cmd
            self.prev_steer[k] = cmd
            # ---- pedal
            err = v_target - v
            pedal = float(np.clip(0.45 * err, -1.0, 1.0))
            if err > 3.0:
                pedal = 1.0
            # traction control: keep the drive force under the estimated rear grip
            if pedal > 0:
                P = w.p
                q = 0.6 * v * v
                fz_r = P["mass"][i] * 9.81 * P["lf"][i] / P["wheelbase"][i] + q * P["cla"][i] * (1 - P["aero_front"][i])
                tfac_r = float(np.clip(1.0 - 0.25 * ((w.temp_r[i] - w.c_t_opt[i]) / w.c_t_width[i]) ** 2, 0.7, 1.0))
                cap = 0.85 * fz_r * P["mu"][i] * P["rear_grip"][i] * grip_now * tfac_r * wfac
                # combined slip: the more the car is cornering, the less traction is left
                ay = abs(v * float(w.r[i]))
                ay_max = 9.81 * P["mu"][i] * grip_now * tfac * (1 + q * P["cla"][i] / (P["mass"][i] * 9.81))
                u = min(ay / max(ay_max, 1.0), 1.0)
                cap *= max(np.sqrt(max(1.0 - u * u, 0.0)), 0.25)
                fdrive = min(P["max_drive"][i], P["power"][i] / max(w.vx[i], 1.0))
                pedal = min(pedal, cap / fdrive)
            # threshold braking: keep each axle's brake force under its estimated grip
            if pedal < 0:
                P = w.p
                q = 0.6 * v * v
                m_ = P["mass"][i]
                wb_ = P["wheelbase"][i]
                df = q * P["cla"][i]
                mu_b = P["mu"][i] * grip_now * tfac * wfac
                decel = 9.81 * mu_b * (1 + df / (m_ * 9.81)) * 0.8
                transfer = m_ * decel * P["h"][i] / wb_
                fz_f = m_ * 9.81 * P["lr"][i] / wb_ + df * P["aero_front"][i] + transfer
                fz_r = max(m_ * 9.81 * P["lf"][i] / wb_ + df * (1 - P["aero_front"][i]) - transfer, 200.0)
                bias = P["brake_front"][i]
                cap_f = mu_b * fz_f / bias
                cap_r = mu_b * P["rear_grip"][i] * fz_r / (1 - bias)
                limit = 0.88 * min(cap_f, cap_r) / P["max_brake"][i]
                ay = abs(v * float(w.r[i]))
                ay_max = 9.81 * mu_b * (1 + df / (m_ * 9.81))
                u = min(ay / max(ay_max, 1.0), 1.0)
                limit *= max(np.sqrt(max(1.0 - u * u, 0.0)), 0.3)
                pedal = max(pedal, -min(limit, 1.0))
            # traction care when the car is sliding
            slip = abs(float(np.arctan2(w.vy[i], max(w.vx[i], 1.0))))
            if slip > 0.12 and pedal > 0:
                pedal *= 0.4
            # ---- strategy
            pit, comp_choice = self._strategy(i, p, wear, wet, comp)
            out[k, 0] = cmd
            out[k, 1] = pedal
            out[k, 2] = 1.0 if pit else -1.0
            out[k, 3 + comp_choice] = 1.0
            if p.cooperative:
                out[k, 7] = wear * 2 - 1
                out[k, 8] = v / 45.0 - 1
        return out

    def _strategy(self, i: int, p: Personality, wear: float, wet: float, comp: int) -> tuple[bool, int]:
        w = self.w
        if w.session.kind != "race":
            return False, comp
        laps_total = self.laps or w.session.laps
        done = int(np.floor(w.progress[i] / w.track.length))
        left = laps_total - done
        on_wets = comp == COMPOUND_INDEX["wet"]
        rain = w.rain
        want_wets = wet > 0.4 or (rain > 0.5 and wet > 0.25)
        want_slicks = wet < 0.18 and rain < 0.15
        if left >= 1 and ((want_wets and not on_wets) or (on_wets and want_slicks)):
            return True, COMPOUND_INDEX["wet"] if want_wets else COMPOUND_INDEX["medium" if left > 5 else "soft"]
        # expected wear at the finish
        if done >= 1:
            per_lap = wear / max(done - self._last_stop_lap(i), 1)
        else:
            per_lap = 0.0
        if left >= 2 and (wear > p.pit_wear or wear + per_lap * (left - 0.5) > 0.97):
            if left <= 4:
                c = "soft"
            elif left <= 8:
                c = "medium"
            else:
                c = "hard"
            if want_wets:
                c = "wet"
            return True, COMPOUND_INDEX[c]
        return False, comp

    def _last_stop_lap(self, i: int) -> int:
        if self.w.pit_stops[i] == 0:
            return 0
        return int(np.floor(self.w.pit_p1[i] / self.w.track.length))
