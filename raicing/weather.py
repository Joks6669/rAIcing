"""Weather: a deterministic rain timeline and a per-cell wetness map."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import WeatherRules


@dataclass
class WeatherScript:
    """Rain intensity as a piecewise-linear function of time."""
    times: list[float] = field(default_factory=lambda: [0.0])
    rain: list[float] = field(default_factory=lambda: [0.0])
    ambient: float = 22.0
    initial_wetness: float = 0.0

    def intensity(self, t: float) -> float:
        return float(np.interp(t, self.times, self.rain))

    def to_dict(self) -> dict:
        return {"times": list(map(float, self.times)), "rain": list(map(float, self.rain)),
                "ambient": float(self.ambient), "initial_wetness": float(self.initial_wetness)}

    @classmethod
    def from_dict(cls, d: dict) -> "WeatherScript":
        return cls(times=list(d["times"]), rain=list(d["rain"]), ambient=d.get("ambient", 22.0),
                   initial_wetness=d.get("initial_wetness", 0.0))

    @classmethod
    def dry(cls, ambient: float = 22.0) -> "WeatherScript":
        return cls(times=[0.0], rain=[0.0], ambient=ambient, initial_wetness=0.0)


def generate_weather(seed: int, duration: float, rules: WeatherRules | None = None,
                     force_rain: bool | None = None) -> WeatherScript:
    rules = rules or WeatherRules()
    rng = np.random.default_rng(seed ^ 0xA11CE)
    ambient = float(rng.uniform(rules.ambient_min, rules.ambient_max))
    rainy = (rng.random() < rules.rain_probability) if force_rain is None else force_rain
    if not rainy:
        return WeatherScript(times=[0.0], rain=[0.0], ambient=ambient)
    ambient -= 6.0
    # one or two rain spells, sometimes starting before the race
    times, rain = [0.0], [0.0]
    t = 0.0
    init_wet = 0.0
    if rng.random() < 0.3:
        level = float(rng.uniform(0.3, 0.9))
        rain[0] = level
        init_wet = float(rng.uniform(0.3, 0.8))
        t_end = float(rng.uniform(0.15, 0.6) * duration)
        times += [t_end, t_end + rng.uniform(30, 90)]
        rain += [level * rng.uniform(0.5, 1.0), 0.0]
        t = times[-1]
    n_spells = int(rng.integers(1, 3))
    for _ in range(n_spells):
        start = t + float(rng.uniform(0.05, 0.5) * duration)
        if start > duration:
            break
        ramp = float(rng.uniform(40, 150))
        hold = float(rng.uniform(60, max(60.0, 0.4 * duration)))   # short sessions: high >= low
        level = float(rng.uniform(0.25, 1.0))
        times += [start, start + ramp, start + ramp + hold, start + 2 * ramp + hold]
        rain += [0.0, level, level * rng.uniform(0.6, 1.0), 0.0]
        t = times[-1]
    return WeatherScript(times=times, rain=rain, ambient=ambient, initial_wetness=init_wet)


class WetnessMap:
    """Wetness in [0, 1] per 10 m cell, updated from the rain intensity."""

    def __init__(self, n_cells: int, cell: float, slow_drying: np.ndarray, rules: WeatherRules,
                 initial: float = 0.0, seed: int = 0):
        self.cell = cell
        self.rules = rules
        rng = np.random.default_rng(seed ^ 0xD0D0)
        self.wet = np.full(n_cells, float(initial))
        # local variation of how fast each cell wets / dries
        self.wet_gain = 1.0 + 0.25 * rng.standard_normal(n_cells).clip(-2, 2)
        dry = np.ones(n_cells)
        sd = np.asarray(slow_drying, dtype=bool)
        if len(sd) == n_cells:
            dry[sd] = 0.35
        self.dry_gain = dry * (1.0 + 0.15 * rng.standard_normal(n_cells).clip(-2, 2))
        if initial > 0:
            self.wet = np.clip(initial * (0.8 + 0.4 * (1.0 - self.dry_gain)), 0.0, 1.0)

    def update(self, rain: float, dt: float) -> None:
        r = self.rules
        if rain > 0.0:
            # wetness drifts towards an equilibrium set by the rain intensity
            target = 0.3 + 0.7 * rain
            wetter = self.wet < target
            self.wet += np.where(wetter, rain * r.wetting_rate * self.wet_gain,
                                 -0.3 * r.drying_rate * self.dry_gain) * dt
        else:
            self.wet -= r.drying_rate * self.dry_gain * dt
        np.clip(self.wet, 0.0, 1.0, out=self.wet)

    def at(self, s: np.ndarray, length: float) -> np.ndarray:
        idx = (np.mod(s, length) / self.cell).astype(np.int64) % len(self.wet)
        return self.wet[idx]
