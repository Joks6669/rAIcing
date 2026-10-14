"""Regulations: every tunable number of the league lives here.

A ``Regulations`` object is a frozen snapshot of the rules for one season. Regulation
changes produce a new object (see ``raicing.league.regulations``). Everything is plain
dataclasses so it serialises to JSON and can be diffed between seasons.
"""
from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass, field
from typing import Any

COMPOUNDS = ("soft", "medium", "hard", "wet")
COMPOUND_INDEX = {c: i for i, c in enumerate(COMPOUNDS)}


@dataclass
class CompoundSpec:
    grip: float          # base friction multiplier on a dry track
    t_opt: float         # optimal temperature (deg C)
    t_width: float       # half width of the temperature window (deg C)
    wear_rate: float     # wear per MJ of sliding energy
    wear_shape: float    # how sharply grip falls with wear (higher = later, sharper cliff)
    wet_loss: float      # grip lost on a fully wet track (fraction)
    dry_overheat: float  # extra heating factor on dry track (wet tyres overheat)


def _default_compounds() -> dict[str, CompoundSpec]:
    return {
        "soft": CompoundSpec(grip=1.00, t_opt=88.0, t_width=22.0, wear_rate=0.0210, wear_shape=1.6, wet_loss=0.55, dry_overheat=1.0),
        "medium": CompoundSpec(grip=0.975, t_opt=95.0, t_width=26.0, wear_rate=0.0120, wear_shape=1.8, wet_loss=0.55, dry_overheat=1.0),
        "hard": CompoundSpec(grip=0.950, t_opt=102.0, t_width=30.0, wear_rate=0.0072, wear_shape=2.0, wet_loss=0.55, dry_overheat=1.0),
        "wet": CompoundSpec(grip=0.880, t_opt=70.0, t_width=24.0, wear_rate=0.0110, wear_shape=1.6, wet_loss=0.10, dry_overheat=2.2),
    }


@dataclass
class CarBase:
    """Baseline car before parts and setup are applied."""
    mass: float = 760.0            # kg, with driver
    yaw_inertia_ratio: float = 1.25  # Iz = ratio * mass (kg m^2 per kg)
    wheelbase: float = 3.4
    cg_to_front: float = 1.75      # distance CG -> front axle (m); rear = wheelbase - this
    cg_height: float = 0.32
    track_width: float = 1.9
    length: float = 5.0
    width: float = 2.0
    power: float = 480_000.0       # W
    max_drive_force: float = 9_000.0  # N, engine/gearbox limit at low speed
    max_brake_force: float = 24_000.0  # N
    brake_bias_front: float = 0.58
    cda: float = 1.30              # drag area (m^2)
    cla: float = 3.40              # downforce area (m^2)
    aero_balance_front: float = 0.43
    rolling_resistance: float = 120.0  # N
    max_steer: float = 0.38        # rad at the wheels
    steer_rate: float = 2.8        # rad/s
    pedal_rate: float = 8.0        # full-scale per second
    mu_peak: float = 1.55          # tyre peak friction on a dry track, fresh soft at t_opt
    load_sensitivity: float = 0.10
    fz_nominal: float = 3500.0     # N per tyre where load_sensitivity = 0
    pacejka_b: float = 12.0
    pacejka_c: float = 1.55
    rear_grip_ratio: float = 1.08       # wider rear tyres: more grip ...
    rear_stiffness_ratio: float = 1.15  # ... and more cornering stiffness -> stable understeer
    tyre_heat_capacity: float = 25_000.0  # J/K per axle
    tyre_heat_gain: float = 0.35         # fraction of sliding power converted to tyre heat
    tyre_cool_base: float = 55.0          # W/K
    tyre_cool_speed: float = 2.5          # W/K per m/s
    tyre_rolling_heat: float = 18.0        # W per (N of load * m/s) * 1e-3 ; carcass flex heating


@dataclass
class TrackRules:
    min_length: float = 3200.0
    max_length: float = 5600.0
    min_width: float = 13.0
    max_width: float = 19.0
    min_radius: float = 16.0       # tightest allowed corner radius on the centreline
    resolution: float = 2.0        # centreline sample spacing (m)
    wet_cell: float = 10.0         # wetness map resolution (m)


@dataclass
class RaceRules:
    physics_dt: float = 0.01
    decision_every: int = 5        # physics steps per decision -> 20 Hz
    target_race_time: float = 1050.0  # s, used to pick 10..15 laps
    min_laps: int = 10
    max_laps: int = 15
    points: tuple = (25, 18, 15, 12, 10, 8, 6, 4, 2, 1)
    pit_entry_before_line: float = 50.0
    pit_exit_after_line: float = 150.0
    pit_lane_time: float = 15.0
    pit_service_time: float = 3.0
    pit_repair_time: float = 6.0
    pit_repair_threshold: float = 0.15
    pit_exit_speed: float = 22.0
    pit_ghost_time: float = 2.0
    pit_tyre_temp: float = 70.0
    start_tyre_temp: float = 60.0
    stall_speed: float = 1.0
    stall_time: float = 20.0
    wrong_way_angle: float = 2.1   # rad
    wrong_way_time: float = 3.0
    finish_grace_factor: float = 1.6  # after leader finishes, others get this * leader avg lap
    max_time_factor: float = 3.0   # hard cap = factor * target race time
    grid_row_gap: float = 8.0
    grid_lateral: float = 3.0
    # stewards
    contact_time_penalty: float = 5.0
    elimination_window: float = 3.0
    team_penalty_points: int = 15
    rear_end_gap: float = 2.5
    side_contact_lat_margin: float = 1.0  # m/s difference to call it a racing incident
    brake_check_window: float = 1.0
    brake_check_speed_ratio: float = 0.7
    damage_per_impulse: float = 1.0 / 9000.0  # damage per N*s of impulse
    collision_restitution: float = 0.25
    wall_contact_eliminates: bool = True


@dataclass
class AeroRules:
    slipstream_range: float = 40.0
    slipstream_drag_cut: float = 0.32
    dirty_air_range: float = 30.0
    dirty_air_downforce_cut: float = 0.22
    wake_half_width: float = 2.6


@dataclass
class WeatherRules:
    rain_probability: float = 0.3
    wetting_rate: float = 1.0 / 90.0    # wetness per second at full rain
    drying_rate: float = 1.0 / 240.0    # wetness per second with no rain
    slow_drying_fraction: float = 0.25  # share of track that dries slowly
    ambient_min: float = 14.0
    ambient_max: float = 32.0


@dataclass
class EconomyRules:
    first_budget: int = 1000
    budget_min: int = 850
    budget_max: int = 1150
    carry_over_fraction: float = 0.2
    cost_growth: float = 1.35
    max_level: int = 10


@dataclass
class TrainingRules:
    season_quota: int = 40_000_000   # decisions per team per season
    max_concurrent_jobs: int = 2
    max_job_wallclock: float = 6 * 3600.0
    max_brain_params: int = 2_000_000
    max_brain_layers: int = 48
    allow_external_brains: bool = False  # official league: brains must come from the lab


@dataclass
class Regulations:
    version: str = "S1"
    compounds: dict = field(default_factory=_default_compounds)
    car: CarBase = field(default_factory=CarBase)
    track: TrackRules = field(default_factory=TrackRules)
    race: RaceRules = field(default_factory=RaceRules)
    aero: AeroRules = field(default_factory=AeroRules)
    weather: WeatherRules = field(default_factory=WeatherRules)
    economy: EconomyRules = field(default_factory=EconomyRules)
    training: TrainingRules = field(default_factory=TrainingRules)

    # ---- serialisation -------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["race"]["points"] = list(self.race.points)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Regulations":
        d = copy.deepcopy(d)
        regs = cls()
        regs.version = d.get("version", regs.version)
        if "compounds" in d:
            regs.compounds = {k: CompoundSpec(**v) for k, v in d["compounds"].items()}
        for name, klass in (("car", CarBase), ("track", TrackRules), ("race", RaceRules),
                            ("aero", AeroRules), ("weather", WeatherRules),
                            ("economy", EconomyRules), ("training", TrainingRules)):
            if name in d:
                values = d[name]
                if name == "race" and "points" in values:
                    values["points"] = tuple(values["points"])
                known = {f.name for f in dataclasses.fields(klass)}
                setattr(regs, name, klass(**{k: v for k, v in values.items() if k in known}))
        return regs

    def copy(self) -> "Regulations":
        return copy.deepcopy(self)

    # ---- dotted access used by regulation changes ------------------------
    def get(self, path: str) -> Any:
        obj: Any = self
        for part in path.split("."):
            obj = obj[part] if isinstance(obj, dict) else getattr(obj, part)
        return obj

    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        obj: Any = self
        for part in parts[:-1]:
            obj = obj[part] if isinstance(obj, dict) else getattr(obj, part)
        if isinstance(obj, dict):
            obj[parts[-1]] = value
        else:
            setattr(obj, parts[-1], value)


DEFAULT_REGULATIONS = Regulations()
