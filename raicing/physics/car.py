"""Car specification: the per-car physical parameters fed to the simulator.

A ``CarSpec`` is derived from the regulations (base car), the team's parts (garage) and the
pre-race setup. The simulator only ever sees ``CarSpec`` values.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..config import CarBase, Regulations

SPEC_FIELDS = (
    "mass", "iz", "lf", "lr", "h", "tw", "length", "width", "power", "max_drive", "max_brake",
    "brake_front", "cda", "cla", "aero_front", "roll_res", "max_steer", "steer_rate", "pedal_rate",
    "mu", "load_sens", "fz_nom", "pac_b", "pac_c", "heat_cap", "heat_gain", "cool_base",
    "cool_speed", "roll_heat", "wear_mult", "rear_grip", "rear_b",
)


@dataclass
class Setup:
    """Pre-race setup chosen by the team director."""
    wing: float = 0.5          # 0 = low downforce / low drag, 1 = high downforce / high drag
    brake_bias: float = 0.58   # front share of braking force
    start_compound: str = "medium"
    quali_compound: str = "soft"

    def clamp(self) -> "Setup":
        self.wing = float(min(max(self.wing, 0.0), 1.0))
        self.brake_bias = float(min(max(self.brake_bias, 0.50), 0.66))
        if self.start_compound not in ("soft", "medium", "hard", "wet"):
            self.start_compound = "medium"
        if self.quali_compound not in ("soft", "medium", "hard", "wet"):
            self.quali_compound = "soft"
        return self


@dataclass
class PerformanceModifiers:
    """Multipliers produced by the garage (parts and technologies). 1.0 = baseline car."""
    power: float = 1.0
    cla: float = 1.0
    cda: float = 1.0
    mass_delta: float = 0.0
    brake: float = 1.0
    cooling: float = 1.0
    mech_grip: float = 1.0
    wear: float = 1.0
    notes: dict = field(default_factory=dict)


@dataclass
class CarSpec:
    mass: float
    iz: float
    lf: float
    lr: float
    h: float
    tw: float
    length: float
    width: float
    power: float
    max_drive: float
    max_brake: float
    brake_front: float
    cda: float
    cla: float
    aero_front: float
    roll_res: float
    max_steer: float
    steer_rate: float
    pedal_rate: float
    mu: float
    load_sens: float
    fz_nom: float
    pac_b: float
    pac_c: float
    heat_cap: float
    heat_gain: float
    cool_base: float
    cool_speed: float
    roll_heat: float
    wear_mult: float
    rear_grip: float = 1.12
    rear_b: float = 1.15
    tyre_window: float = 1.0   # multiplier of every compound's optimal temperature window width

    def to_dict(self) -> dict:
        return asdict(self)


def build_spec(regs: Regulations | None = None, mods: PerformanceModifiers | None = None,
               setup: Setup | None = None) -> CarSpec:
    regs = regs or Regulations()
    mods = mods or PerformanceModifiers()
    setup = (setup or Setup()).clamp()
    b: CarBase = regs.car
    mass = b.mass + mods.mass_delta
    wing_cla = 0.84 + 0.32 * setup.wing
    wing_cda = 0.87 + 0.26 * setup.wing
    return CarSpec(
        mass=mass,
        iz=b.yaw_inertia_ratio * mass,
        lf=b.cg_to_front,
        lr=b.wheelbase - b.cg_to_front,
        h=b.cg_height,
        tw=b.track_width,
        length=b.length,
        width=b.width,
        power=b.power * mods.power,
        max_drive=b.max_drive_force * min(mods.power, 1.1),
        max_brake=b.max_brake_force * mods.brake,
        brake_front=setup.brake_bias,
        cda=b.cda * mods.cda * wing_cda,
        cla=b.cla * mods.cla * wing_cla,
        aero_front=b.aero_balance_front,
        roll_res=b.rolling_resistance,
        max_steer=b.max_steer,
        steer_rate=b.steer_rate,
        pedal_rate=b.pedal_rate,
        mu=b.mu_peak * mods.mech_grip,
        load_sens=b.load_sensitivity,
        fz_nom=b.fz_nominal,
        pac_b=b.pacejka_b,
        pac_c=b.pacejka_c,
        heat_cap=b.tyre_heat_capacity,
        heat_gain=b.tyre_heat_gain,
        cool_base=b.tyre_cool_base,
        cool_speed=b.tyre_cool_speed,
        roll_heat=b.tyre_rolling_heat,
        wear_mult=mods.wear,
        rear_grip=b.rear_grip_ratio,
        rear_b=b.rear_stiffness_ratio,
        # the cooling part keeps the tyres in their window (wider window, hot or cold): cooling them
        # harder would push them below their window in normal conditions and only cost grip
        tyre_window=mods.cooling,
    )
