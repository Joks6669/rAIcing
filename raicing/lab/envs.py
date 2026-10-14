"""Training environments.

* ``LabSim``: the simulation core shared by every adapter. It owns one or several ``World``s
  ("arenas"), places learner cars, steps opponents, computes rewards, detects the end of
  episodes and counts the quota at the source.
* ``LabVecEnv``: stable-baselines3 ``VecEnv`` over the learner cars of a ``LabSim``.
* ``RaicingEnv``: gymnasium ``Env`` driving one car.
* ``RaicingParallelEnv``: PettingZoo ``ParallelEnv`` where several learner agents share one race.

Curriculum modes (``docs/LAB.md``):

* ``solo``: one ``World(kind="lab", interactions=False)`` with ``n`` independent ghost cars; every car
  restarts on its own after an elimination or ``episode_decisions`` decisions, at a random place,
  lateral offset, speed (15–60 m/s, capped by the corner ahead), heading, and (with
  ``tyre_randomization``) random compound, wear (0–0.6) and temperature (60–110 °C).
* ``race``: one ``World(kind="race", interactions=False)`` with ``n`` ghost cars, each running its own
  ``laps``-lap race (pit stops enabled). The chequered flag of one ghost must not end the others'
  races, so the flag is lifted after every decision and each car finishes on its own lap count.
* ``traffic``: ``ceil(n / learners_per_world)`` small ``World(kind="race", interactions=True)`` races,
  each with 1–2 learner cars (teammates, radio active) and reference / frozen-brain opponents, from
  a standing start at a random grid slot.

Episodes also end (as an elimination) when a car makes no progress for ``stall_time`` seconds or
drives the wrong way longer than the regulations allow — the lab world does not eliminate for those.
Tracks are drawn from the lab tracks only (``lab_track``) and rotated every ``track_rotation``
decisions (solo/race: all episodes are truncated; traffic: the next races use the new track).

In solo and race modes the other cars are ghosts that a pilot would never see, so the race-block
features computed against them (position, gaps ahead/behind) are replaced by what a lone car
observes (zeros). Everything else is exactly ``sensors.observe``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import gymnasium as gym
import numpy as np
from stable_baselines3.common.vec_env.base_vec_env import VecEnv, VecEnvIndices, VecEnvStepReturn

from ..brains.spec import Brain
from ..config import COMPOUND_INDEX, Regulations
from ..drivers.reference import PERSONALITIES
from ..physics.car import CarSpec, build_spec
from ..race import estimate_lap_time
from ..sensors import OBS_DIM, observe, obs_slices
from ..weather import WeatherScript
from ..world import ACT_DIM, DNF, DSQ, FINISHED, PIT, RACING, SessionConfig, World
from .evaluation import lab_track
from .quota import QuotaCounter
from .rewards import RewardWeights, compute_rewards, snapshot
from .teachers import CachedReferenceDriver

MODES = ("solo", "race", "traffic")
_RACE = obs_slices()["race"]
_GHOST_FEATURES = slice(_RACE.start + 5, _RACE.start + 8)   # position, gap ahead, gap behind

BrainResolver = Callable[[str], Brain]


@dataclass
class EnvSettings:
    """Everything a ``LabSim`` needs; built from a validated job config by ``settings_from_config``."""
    mode: str = "solo"
    n: int = 64
    out_dim: int = 2
    laps: int = 3
    opponents: list[str] = field(default_factory=lambda: ["pro", "prudent"])
    rain_fraction: float = 0.0
    tyre_randomization: bool = True
    track_rotation: int = 4000
    episode_decisions: int = 1500
    stall_time: float = 10.0
    learners_per_world: int = 1
    track_pool: int = 1000
    tracks: list[int] | None = None
    reward: RewardWeights = field(default_factory=RewardWeights)
    seed: int = 0

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode de curriculum inconnu : {self.mode!r}")
        if not 2 <= self.out_dim <= ACT_DIM:
            raise ValueError(f"le nombre de sorties doit être entre 2 et {ACT_DIM}")


def settings_from_config(cfg: dict[str, Any], n: int | None = None) -> EnvSettings:
    """``EnvSettings`` from a config validated by ``raicing.lab.jobs.validate_config``."""
    cur = cfg["curriculum"]
    return EnvSettings(
        mode=cur["mode"], n=int(n if n is not None else cfg["envs"]), out_dim=int(cfg["architecture"]["outputs"]),
        laps=int(cur["laps"]), opponents=list(cur["opponents"]), rain_fraction=float(cur["rain_fraction"]),
        tyre_randomization=bool(cur["tyre_randomization"]), track_rotation=int(cur["track_rotation"]),
        episode_decisions=int(cur["episode_decisions"]), stall_time=float(cur["stall_time"]),
        learners_per_world=int(cur["learners_per_world"]), track_pool=int(cur["track_pool"]),
        tracks=list(cur["tracks"]) if cur.get("tracks") else None,
        reward=RewardWeights.from_dict(cfg["reward"]), seed=int(cfg["seed"]))


def full_actions(a: np.ndarray, out_dim: int) -> np.ndarray:
    """Map a (k, out_dim) learner action to the (k, ACT_DIM) world action exactly like ``Brain.act``."""
    a = np.clip(np.asarray(a, dtype=np.float64).reshape(-1, out_dim), -1.0, 1.0)
    out = np.zeros((len(a), ACT_DIM))
    out[:, :out_dim] = a
    if out_dim <= 2:
        out[:, 2] = -1.0
    return out


class _BrainOpponents:
    """Frozen brains driving some cars of a world (batched per brain)."""

    def __init__(self, world: World, groups: list[tuple[Brain, list[int]]]):
        self.w = world
        self.groups = [(b, np.asarray(cars, dtype=np.int64), b.init_state(len(cars))) for b, cars in groups]

    def act(self, out: np.ndarray) -> None:
        for g, (brain, cars, state) in enumerate(self.groups):
            obs = observe(self.w, cars)
            a, st = brain.act(obs, state)
            self.groups[g] = (brain, cars, st)
            live = self.w.status[cars] == RACING
            out[cars[live]] = a[live]


class _Arena:
    """One world, its learner cars and its opponents."""

    def __init__(self, world: World, learners: np.ndarray, ref: CachedReferenceDriver | None,
                 brains: _BrainOpponents | None, ghosts: bool):
        self.w = world
        self.learners = np.asarray(learners, dtype=np.int64)
        self.ref = ref
        self.brains = brains
        self.ghosts = ghosts
        m = len(self.learners)
        self.ep_len = np.zeros(m, dtype=np.int64)
        self.ep_ret = np.zeros(m)
        self.best_prog = np.zeros(m)
        self.best_t = np.zeros(m)
        self.prev_steer = np.zeros(m)

    def observe(self, rows: np.ndarray | None = None) -> np.ndarray:
        cars = self.learners if rows is None else self.learners[rows]
        o = observe(self.w, cars)
        if self.ghosts:
            o[:, _GHOST_FEATURES] = 0.0
        return o

    def start_rows(self, rows: np.ndarray) -> None:
        cars = self.learners[rows]
        self.ep_len[rows] = 0
        self.ep_ret[rows] = 0.0
        self.best_prog[rows] = self.w.progress[cars]
        self.best_t[rows] = self.w.t
        self.prev_steer[rows] = 0.0


class LabSim:
    """Simulation core: learner cars in one or several worlds, rewards, resets and quota."""

    def __init__(self, settings: EnvSettings, regs: Regulations | None = None, quota: QuotaCounter | None = None,
                 brain_resolver: BrainResolver | None = None, spec: CarSpec | None = None,
                 auto_reset: bool = True):
        self.cfg = settings
        self.regs = regs or Regulations()
        self.spec = spec or build_spec(self.regs)
        self.quota = quota
        self.resolver = brain_resolver
        self.auto_reset = auto_reset
        self.rng = np.random.default_rng(settings.seed)
        self.n = settings.n
        self.decisions = 0              # world decisions since the start
        self.since_rotation = 0
        self.track_k = self._pick_track()
        self.arenas: list[_Arena] = []
        self.env_map: list[tuple[int, int]] = []    # env index -> (arena, row)
        self._opp_brains: dict[str, Brain] = {}
        for o in settings.opponents if settings.mode == "traffic" else []:
            if o not in PERSONALITIES:
                if self.resolver is None:
                    raise ValueError(f"adversaire inconnu : {o!r} (ni personnalité de référence, ni cerveau résolu)")
                self._opp_brains[o] = self.resolver(o)
        self.last_info: dict[str, Any] = {}

    # ------------------------------------------------------------------ tracks / weather
    def _pick_track(self) -> int:
        if self.cfg.tracks:
            return int(self.rng.choice(self.cfg.tracks))
        return int(self.rng.integers(0, max(1, self.cfg.track_pool)))

    def _weather(self) -> WeatherScript:
        if self.rng.random() < self.cfg.rain_fraction:
            level = float(self.rng.uniform(0.3, 1.0))
            return WeatherScript(times=[0.0], rain=[level], ambient=float(self.rng.uniform(12, 20)),
                                 initial_wetness=level)
        return WeatherScript.dry(float(self.rng.uniform(14, 32)))

    # ------------------------------------------------------------------ building
    def _build(self) -> None:
        track = lab_track(self.track_k)
        mode = self.cfg.mode
        self.arenas = []
        self.env_map = []
        if mode in ("solo", "race"):
            kind = "lab" if mode == "solo" else "race"
            w = World(track, [self.spec] * self.n, self.regs, self._weather(),
                      SessionConfig(kind=kind, laps=self.cfg.laps, interactions=False),
                      seed=int(self.rng.integers(1 << 30)))
            arena = _Arena(w, np.arange(self.n), None, None, ghosts=True)
            self.arenas.append(arena)
            self.env_map = [(0, i) for i in range(self.n)]
            for i in range(self.n):
                self._place(arena, i)
            arena.start_rows(np.arange(self.n))
        else:
            per = max(1, self.cfg.learners_per_world)
            k = 0
            while k < self.n:
                m = min(per, self.n - k)
                self.arenas.append(self._traffic_arena(track, m))
                a = len(self.arenas) - 1
                self.env_map += [(a, r) for r in range(m)]
                k += m

    def _traffic_arena(self, track, m: int, weather: WeatherScript | None = None) -> _Arena:
        opps = list(self.cfg.opponents)
        n_cars = m + len(opps)
        # learners are cars 0..m-1 (same team when m == 2); opponents follow, one team each
        teams = [0] * m + list(range(1, len(opps) + 1))
        lap_est = estimate_lap_time(track, self.regs)
        sess = SessionConfig(kind="race", laps=self.cfg.laps, interactions=True,
                             max_time=self.regs.race.max_time_factor * max(self.cfg.laps * lap_est, 60.0))
        w = World(track, [self.spec] * n_cars, self.regs, weather or self._weather(), sess, teams=teams,
                  seed=int(self.rng.integers(1 << 30)))
        order = [int(x) for x in self.rng.permutation(n_cars)]
        wet = w.rain > 0.3
        comps = []
        for _ in range(n_cars):
            if wet:
                comps.append("wet" if self.rng.random() < 0.7 else "medium")
            else:
                comps.append(str(self.rng.choice(["soft", "medium", "hard"])))
        w.setup_grid(order, comps)
        ref_cars = [m + j for j, o in enumerate(opps) if o in PERSONALITIES]
        ref = CachedReferenceDriver(w, ref_cars, [o for o in opps if o in PERSONALITIES],
                                    laps=self.cfg.laps) if ref_cars else None
        groups: dict[str, list[int]] = {}
        for j, o in enumerate(opps):
            if o not in PERSONALITIES:
                groups.setdefault(o, []).append(m + j)
        brains = _BrainOpponents(w, [(self._opp_brains[o], cars) for o, cars in groups.items()]) if groups else None
        arena = _Arena(w, np.arange(m), ref, brains, ghosts=False)
        arena.start_rows(np.arange(m))
        return arena

    def _place(self, arena: _Arena, row: int) -> None:
        """Random (re)start of one ghost car (solo / race modes)."""
        w = arena.w
        i = int(arena.learners[row])
        tr = w.track
        rng = self.rng
        wet = w.rain > 0.3
        if self.cfg.mode == "race":
            if rng.random() < 0.25:     # standing start on the grid line
                s, lat, v, prog = tr.length - 10.0, float(rng.uniform(-3, 3)), 0.0, -10.0
            else:
                s = float(rng.uniform(0, tr.length))
                hw = float(tr.width_at(s))
                lat = float(rng.uniform(-1, 1) * max(hw - 3.5, 0.0))
                vcap = 0.8 * float(w.vmin_ahead[int(tr.index_at(s))])
                v = float(rng.uniform(15.0, max(16.0, min(60.0, vcap))))
                prog = s
            comp = ("wet" if rng.random() < 0.7 else "medium") if wet else str(rng.choice(["soft", "medium", "hard"]))
            w.place_car(i, s, lat, v, float(rng.normal(0, 0.03)), comp, self.regs.race.start_tyre_temp, 0.0,
                        progress=prog)
            # forget the previous race of this car
            w.lap_times[i] = []
            w.finish_time[i] = np.nan
            w.end_time[i] = np.nan
            w.finish_order[i] = -1
            w.pit_stops[i] = 0
            w.pit_request[i] = False
            w.penalty_time[i] = 0.0
            w.messages[i] = 0.0
            w._pit_new.pop(i, None)
            return
        s = float(rng.uniform(0, tr.length))
        hw = float(tr.width_at(s))
        lat = float(rng.uniform(-1, 1) * max(hw - 3.5, 0.0))
        vcap = 0.8 * float(w.vmin_ahead[int(tr.index_at(s))])
        v = float(rng.uniform(15.0, max(16.0, min(60.0, vcap))))
        if self.cfg.tyre_randomization:
            if wet:
                comp = "wet" if rng.random() < 0.5 else str(rng.choice(["soft", "medium", "hard"]))
            else:
                comp = "wet" if rng.random() < 0.05 else str(rng.choice(["soft", "medium", "hard"]))
            wear = float(rng.uniform(0.0, 0.6))
            temp = float(rng.uniform(60.0, 110.0))
        else:
            comp, wear, temp = ("wet" if wet else "medium"), 0.0, self.regs.race.start_tyre_temp
        w.place_car(i, s, lat, v, float(rng.normal(0, 0.05)), comp, temp, wear, progress=s)
        w.lap_times[i] = []

    # ------------------------------------------------------------------ API
    def reset(self) -> np.ndarray:
        """Rebuild everything on a fresh track and return the (n, OBS_DIM) observation."""
        self.since_rotation = 0
        self._build()
        return self._observe_all()

    def _observe_all(self) -> np.ndarray:
        obs = np.zeros((self.n, OBS_DIM), dtype=np.float32)
        k = 0
        for arena in self.arenas:
            m = len(arena.learners)
            obs[k:k + m] = arena.observe()
            k += m
        return obs

    def step(self, actions: np.ndarray, active: np.ndarray | None = None
             ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[dict]]:
        """One decision for every learner car.

        Returns ``obs, rewards, terminated, truncated, infos``. With ``auto_reset`` the cars whose
        episode ended are restarted and ``obs`` holds their first observation; the last one is in
        ``infos[i]["terminal_observation"]``.

        ``active`` (bool per env, used without ``auto_reset``) marks the cars still in their episode:
        only those count against the quota; the others brake and their outputs must be ignored."""
        actions = np.asarray(actions, dtype=np.float64).reshape(self.n, -1).copy()
        if active is not None:
            active = np.asarray(active, dtype=bool)
            actions[~active] = 0.0
            actions[~active, 1] = -1.0
        if self.quota is not None:
            self.quota.consume(self.n if active is None else int(active.sum()))
        self.decisions += 1
        self.since_rotation += 1
        rewards = np.zeros(self.n)
        terminated = np.zeros(self.n, dtype=bool)
        truncated = np.zeros(self.n, dtype=bool)
        infos: list[dict] = [{} for _ in range(self.n)]
        obs = np.zeros((self.n, OBS_DIM), dtype=np.float32)
        rotate = self.cfg.mode != "traffic" and self.since_rotation >= self.cfg.track_rotation
        k = 0
        for a_idx, arena in enumerate(self.arenas):
            m = len(arena.learners)
            sl = slice(k, k + m)
            r, term, trunc, reasons = self._step_arena(arena, actions[sl])
            rewards[sl] = r
            o = arena.observe()
            arena_done = False
            if self.cfg.mode == "traffic":
                if term.any() or trunc.any() or arena.w.done():
                    # the race is over for this arena: end every learner's episode together
                    trunc = trunc | ~term
                    arena_done = True
            elif rotate:
                trunc = trunc | ~term
            done = term | trunc
            for row in np.nonzero(done & ~term)[0]:
                if not reasons[row]:
                    reasons[row] = "race_over" if self.cfg.mode == "traffic" else "track_rotation"
            for row in np.nonzero(done)[0]:
                e = k + int(row)
                infos[e]["terminal_observation"] = o[row].copy()
                car = int(arena.learners[row])
                infos[e]["episode"] = {"r": float(arena.ep_ret[row]), "l": int(arena.ep_len[row]), "t": 0.0,
                                       "reason": reasons[row], "laps": len(arena.w.lap_times[car]),
                                       "lap_times": [round(float(x), 3) for x in arena.w.lap_times[car]],
                                       "track": self.track_k}
            terminated[sl] = term
            truncated[sl] = trunc
            if self.auto_reset and done.any() and not rotate:
                if arena_done:
                    self.arenas[a_idx] = arena = self._traffic_arena(lab_track(self.track_k), m)
                    o = arena.observe()
                else:
                    rows = np.nonzero(done)[0]
                    for row in rows:
                        self._place(arena, int(row))
                    arena.start_rows(rows)
                    o[rows] = arena.observe(rows)
            obs[sl] = o
            k += m
        if rotate:
            self.track_k = self._pick_track()
            if self.auto_reset:
                obs = self.reset()
        elif self.cfg.mode == "traffic" and self.since_rotation >= self.cfg.track_rotation:
            self.since_rotation = 0
            self.track_k = self._pick_track()
        return obs, rewards, terminated, truncated, infos

    def _step_arena(self, arena: _Arena, act: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
        w = arena.w
        cars = arena.learners
        m = len(cars)
        full = np.zeros((w.n, ACT_DIM))
        la = full_actions(act, self.cfg.out_dim)
        full[cars] = la
        if arena.ref is not None:
            full[arena.ref.cars] = arena.ref.act()
        if arena.brains is not None:
            arena.brains.act(full)
        before = snapshot(w, cars)
        was_running = np.isin(before.status, (RACING, PIT))
        w.step(full)
        if self.cfg.mode == "race":
            # independent races: one ghost's chequered flag must not end the others' races
            w.flag_time = None
            w.grace_deadline = None
        # environment-level eliminations (the lab world does not eliminate for these)
        eliminated = np.zeros(m, dtype=bool)
        reasons = [""] * m
        rr = self.regs.race
        prog = w.progress[cars]
        better = prog > arena.best_prog + 1.0
        arena.best_prog = np.where(better, prog, arena.best_prog)
        arena.best_t = np.where(better, w.t, arena.best_t)
        racing = w.status[cars] == RACING
        stalled = racing & (w.t - arena.best_t > self.cfg.stall_time)
        wrong = racing & (w.wrong_timer[cars] > rr.wrong_way_time)
        for row in np.nonzero(stalled | wrong)[0]:
            eliminated[row] = True
            reasons[row] = "stalled" if stalled[row] else "wrong_way"
            if self.cfg.mode == "traffic":
                w._eliminate(int(cars[row]), reasons[row])
        after = snapshot(w, cars)
        steer = la[:, 0]
        r, parts = compute_rewards(self.cfg.reward, w, cars, before, after, steer - arena.prev_steer,
                                   eliminated & was_running, use_position=self.cfg.mode == "traffic")
        arena.prev_steer = steer
        arena.ep_len += 1
        arena.ep_ret += r
        out = np.isin(after.status, (DNF, DSQ)) & was_running
        fin = (after.status == FINISHED) & was_running
        for row in np.nonzero(out)[0]:
            reasons[row] = w.reason[int(cars[row])] or "out"
        for row in np.nonzero(fin)[0]:
            reasons[row] = "finished"
        term = out | fin | eliminated
        trunc = ~term & (arena.ep_len >= self.cfg.episode_decisions) if self.cfg.mode != "traffic" \
            else np.zeros(m, dtype=bool)
        for row in np.nonzero(trunc)[0]:
            reasons[row] = "time_limit"
        return r, term, trunc, reasons

    # ------------------------------------------------------------------ helpers for algorithms
    def worlds(self) -> list[World]:
        return [a.w for a in self.arenas]


# ====================================================================== SB3 VecEnv
class LabVecEnv(VecEnv):
    """stable-baselines3 ``VecEnv``: one env per learner car of a ``LabSim``."""

    render_mode = None

    def __init__(self, sim: LabSim):
        self.sim = sim
        low = np.full(sim.cfg.out_dim, -1.0, dtype=np.float32)
        obs_space = gym.spaces.Box(-np.inf, np.inf, (OBS_DIM,), dtype=np.float32)
        act_space = gym.spaces.Box(low, -low, dtype=np.float32)
        super().__init__(sim.n, obs_space, act_space)
        self._actions: np.ndarray | None = None

    def reset(self) -> np.ndarray:
        obs = self.sim.reset()
        self._reset_seeds()
        self._reset_options()
        return obs

    def step_async(self, actions: np.ndarray) -> None:
        self._actions = np.asarray(actions)

    def step_wait(self) -> VecEnvStepReturn:
        assert self._actions is not None, "step_async must be called first"
        obs, rew, term, trunc, infos = self.sim.step(self._actions)
        self._actions = None
        dones = term | trunc
        for i in np.nonzero(dones)[0]:
            infos[i]["TimeLimit.truncated"] = bool(trunc[i] and not term[i])
        return obs, rew.astype(np.float32), dones, infos

    def close(self) -> None:
        pass

    def seed(self, seed: int | None = None) -> Sequence[None | int]:  # type: ignore[override]
        if seed is not None:
            self.sim.rng = np.random.default_rng(seed)
        return [seed] * self.num_envs

    def _indices(self, indices: VecEnvIndices) -> list[int]:
        if indices is None:
            return list(range(self.num_envs))
        if isinstance(indices, int):
            return [indices]
        return list(indices)

    def get_attr(self, attr_name: str, indices: VecEnvIndices = None) -> list[Any]:
        value = getattr(self, attr_name)
        return [value for _ in self._indices(indices)]

    def set_attr(self, attr_name: str, value: Any, indices: VecEnvIndices = None) -> None:
        setattr(self, attr_name, value)

    def env_method(self, method_name: str, *method_args, indices: VecEnvIndices = None, **method_kwargs) -> list[Any]:
        fn = getattr(self, method_name)
        return [fn(*method_args, **method_kwargs) for _ in self._indices(indices)]

    def env_is_wrapped(self, wrapper_class: type[gym.Wrapper], indices: VecEnvIndices = None) -> list[bool]:
        return [False for _ in self._indices(indices)]


# ====================================================================== Gymnasium (1 car)
class RaicingEnv(gym.Env):
    """Single-car Gymnasium environment (solo, race or traffic with one learner car)."""

    metadata = {"render_modes": []}

    def __init__(self, settings: EnvSettings | None = None, regs: Regulations | None = None,
                 quota: QuotaCounter | None = None, brain_resolver: BrainResolver | None = None):
        settings = settings or EnvSettings()
        settings.n = 1
        settings.learners_per_world = 1
        self.settings = settings
        self.regs = regs
        self.quota = quota
        self.resolver = brain_resolver
        self.sim = LabSim(settings, regs, quota, brain_resolver, auto_reset=False)
        self.observation_space = gym.spaces.Box(-np.inf, np.inf, (OBS_DIM,), dtype=np.float32)
        self.action_space = gym.spaces.Box(-1.0, 1.0, (settings.out_dim,), dtype=np.float32)
        self._needs_reset = True

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        if seed is not None:
            self.sim.rng = np.random.default_rng(seed)
            self.sim.track_k = self.sim._pick_track()
        obs = self.sim.reset()
        self._needs_reset = False
        return obs[0], {"track": self.sim.track_k}

    def step(self, action):
        if self._needs_reset:
            raise RuntimeError("appelez reset() avant step()")
        obs, rew, term, trunc, infos = self.sim.step(np.asarray(action)[None, :])
        info = infos[0]
        if term[0] or trunc[0]:
            self._needs_reset = True
            obs0 = info.pop("terminal_observation", obs[0])
        else:
            obs0 = obs[0]
        return obs0, float(rew[0]), bool(term[0]), bool(trunc[0]), info


# ====================================================================== PettingZoo
try:  # pragma: no cover - import guard
    from pettingzoo import ParallelEnv as _ParallelBase
except Exception:  # pragma: no cover
    _ParallelBase = object  # type: ignore[assignment,misc]


class RaicingParallelEnv(_ParallelBase):  # type: ignore[misc,valid-type]
    """PettingZoo ``ParallelEnv``: ``n_agents`` learner cars racing each other (and optional
    reference / frozen-brain opponents) in one ``World(kind="race", interactions=True)``.

    Agents ``car_0``, ``car_1``... are removed from ``agents`` when their race ends."""

    metadata = {"name": "raicing_parallel_v1", "render_modes": []}

    def __init__(self, n_agents: int = 2, settings: EnvSettings | None = None, regs: Regulations | None = None,
                 quota: QuotaCounter | None = None, brain_resolver: BrainResolver | None = None,
                 teams: Sequence[int] | None = None):
        settings = settings or EnvSettings(mode="traffic", opponents=[])
        settings.mode = "traffic"
        settings.n = n_agents
        settings.learners_per_world = n_agents
        self.settings = settings
        self.sim = LabSim(settings, regs, quota, brain_resolver, auto_reset=False)
        self.possible_agents = [f"car_{i}" for i in range(n_agents)]
        self.agents: list[str] = []
        self._teams = list(teams) if teams is not None else list(range(n_agents))
        self._obs_space = gym.spaces.Box(-np.inf, np.inf, (OBS_DIM,), dtype=np.float32)
        self._act_space = gym.spaces.Box(-1.0, 1.0, (settings.out_dim,), dtype=np.float32)

    def observation_space(self, agent: str) -> gym.spaces.Box:
        return self._obs_space

    def action_space(self, agent: str) -> gym.spaces.Box:
        return self._act_space

    def reset(self, seed: int | None = None, options: dict | None = None):
        if seed is not None:
            self.sim.rng = np.random.default_rng(seed)
            self.sim.track_k = self.sim._pick_track()
        self.sim.since_rotation = 0
        track = lab_track(self.sim.track_k)
        arena = self.sim._traffic_arena(track, len(self.possible_agents))
        w = arena.w
        n = len(self.possible_agents)
        # teams for the learner agents (default: every agent its own team)
        base = int(w.teams.max()) + 1
        for i, t in enumerate(self._teams):
            w.teams[i] = base + int(t)
        w.teammate[:] = -1
        for i in range(w.n):
            for j in range(w.n):
                if i != j and w.teams[i] == w.teams[j]:
                    w.teammate[i] = j
        self.sim.arenas = [arena]
        self.sim.env_map = [(0, r) for r in range(n)]
        self.agents = list(self.possible_agents)
        obs = arena.observe()
        return ({a: obs[i] for i, a in enumerate(self.possible_agents)},
                {a: {} for a in self.possible_agents})

    def step(self, actions: dict[str, np.ndarray]):
        arena = self.sim.arenas[0]
        n = len(self.possible_agents)
        act = np.zeros((n, self.settings.out_dim))
        for i, a in enumerate(self.possible_agents):
            if a in actions:
                act[i] = np.asarray(actions[a]).reshape(-1)[: self.settings.out_dim]
        live = [i for i, a in enumerate(self.possible_agents) if a in self.agents]
        if self.sim.quota is not None:
            self.sim.quota.consume(len(live))
        r, term, trunc, reasons = self.sim._step_arena(arena, act)
        if arena.w.done():
            trunc = trunc | ~term
        obs = arena.observe()
        out_obs, out_r, out_term, out_trunc, out_inf = {}, {}, {}, {}, {}
        for i in live:
            a = self.possible_agents[i]
            out_obs[a] = obs[i]
            out_r[a] = float(r[i])
            out_term[a] = bool(term[i])
            out_trunc[a] = bool(trunc[i] and not term[i])
            out_inf[a] = {"reason": reasons[i]} if (term[i] or trunc[i]) else {}
        self.agents = [a for a in self.agents if not (out_term[a] or out_trunc[a])]
        return out_obs, out_r, out_term, out_trunc, out_inf
