"""Fast tests of the training lab (target: well under 90 s on 2 cores)."""
from __future__ import annotations

import numpy as np
import pytest
import torch
import torch.nn as nn

from raicing.brains.spec import Brain, mlp_brain
from raicing.config import Regulations
from raicing.lab.envs import (EnvSettings, LabSim, LabVecEnv, RaicingEnv, RaicingParallelEnv, full_actions,
                              settings_from_config)
from raicing.lab.export import GeluTanh, export_ppo, export_sac, export_sequential, export_td3, parse_mlp
from raicing.lab.jobs import JobResult, QuotaExceeded, run_job, validate_config
from raicing.lab.normalize import LabVecNormalize, ObsStats, export_std, normalize
from raicing.lab.quota import QuotaCounter
from raicing.lab.rewards import RewardWeights
from raicing.sensors import OBS_DIM
from raicing.world import ACT_DIM

torch.set_num_threads(2)
REGS = Regulations()


def _cfg(**kw):
    base = {"method": "ppo", "envs": 4, "architecture": {"hidden": [16, 16], "outputs": 2}}
    base.update(kw)
    return validate_config(base)


def _random_weights_(module: nn.Module, seed: int = 0) -> None:
    g = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for p in module.parameters():
            p.copy_(torch.randn(p.shape, generator=g) * 0.3)


# ---------------------------------------------------------------------- config
def test_validate_config_defaults():
    cfg = validate_config({})
    assert cfg["method"] == "ppo"
    assert cfg["architecture"]["hidden"] == [256, 256]
    assert cfg["architecture"]["squash"] == "clip"
    assert cfg["curriculum"]["mode"] == "solo"
    assert cfg["reward"]["crash"] == -20.0
    assert validate_config({"method": "sac"})["architecture"]["squash"] == "tanh"
    assert validate_config({"method": "es", "architecture": {"gru": 16}})["architecture"]["gru"] == 16


@pytest.mark.parametrize("bad, fragment", [
    ({"method": "dqn"}, "méthode inconnue"),
    ({"foo": 1}, "clé(s) inconnue(s)"),
    ({"steps": 0}, "steps"),
    ({"architecture": {"outputs": 12}}, "outputs"),
    ({"architecture": {"activation": "swish"}}, "activation inconnue"),
    ({"method": "ppo", "architecture": {"gru": 8}}, "GRU"),
    ({"method": "sac", "architecture": {"layernorm": True}}, "layernorm"),
    ({"method": "ppo", "architecture": {"squash": "tanh"}}, "clip"),
    ({"architecture": {"hidden": [4096]}}, "hidden"),
    ({"reward": {"speed": 1.0}}, "reward"),
    ({"curriculum": {"mode": "rally"}}, "mode de curriculum"),
    ({"curriculum": {"tracks": [20_000_000]}}, "validation"),
    ({"hyperparams": {"nonsense": 1}}, "hyperparamètre"),
    ({"method": "es", "hyperparams": {"population": 7}}, "population"),
    ({"method": "bc", "hyperparams": {"teacher": "schumi"}}, "professeur"),
])
def test_validate_config_errors(bad, fragment):
    with pytest.raises(ValueError) as e:
        validate_config(bad)
    assert fragment in str(e.value)


# ---------------------------------------------------------------------- quota
def test_quota_counter_exact():
    q = QuotaCounter(10)
    q.consume(4)
    q.consume(6)
    assert q.used == 10 and q.exhausted
    with pytest.raises(QuotaExceeded):
        q.consume(1)
    assert q.used == 10


def test_env_quota_exact():
    q = QuotaCounter(4 * 7 + 3)
    env = LabVecEnv(LabSim(settings_from_config(_cfg()), REGS, q))
    env.reset()
    a = np.zeros((4, 2), dtype=np.float32)
    for _ in range(7):
        env.step(a)
    assert q.used == 28
    with pytest.raises(QuotaExceeded):
        env.step(a)
    assert q.used == 28          # a refused step counts nothing


def test_run_job_never_exceeds_quota(tmp_path):
    cfg = {"method": "ppo", "steps": 10_000, "envs": 4, "architecture": {"hidden": [8], "outputs": 2},
           "hyperparams": {"n_steps": 8, "batch_size": 32}}
    res = run_job(cfg, REGS, None, 50, tmp_path, evaluate=False)
    assert res.steps_used <= 50 and res.steps_used == 48
    with pytest.raises(QuotaExceeded):
        run_job(cfg, REGS, None, 0, tmp_path, evaluate=False)


# ---------------------------------------------------------------------- environments
@pytest.mark.parametrize("mode", ["solo", "race", "traffic"])
def test_vecenv_shapes(mode):
    cfg = _cfg(curriculum={"mode": mode, "laps": 1, "opponents": ["pro"], "track_rotation": 100,
                           "rain_fraction": 0.5})
    env = LabVecEnv(LabSim(settings_from_config(cfg), REGS, QuotaCounter(10_000)))
    obs = env.reset()
    assert obs.shape == (4, OBS_DIM) and obs.dtype == np.float32
    rng = np.random.default_rng(0)
    saw_done = False
    for _ in range(120):
        a = rng.uniform(-1, 1, (4, 2)).astype(np.float32)
        a[:, 1] = 1.0            # full throttle: random steering then hits a wall quickly in every mode
        obs, rew, done, infos = env.step(a)
        assert obs.shape == (4, OBS_DIM) and rew.shape == (4,) and done.shape == (4,) and len(infos) == 4
        assert np.all(np.isfinite(obs)) and np.all(np.isfinite(rew))
        for i in np.nonzero(done)[0]:
            saw_done = True
            assert infos[i]["terminal_observation"].shape == (OBS_DIM,)
            assert "TimeLimit.truncated" in infos[i] and "episode" in infos[i]
    assert saw_done          # random drivers crash (solo/race: the track also rotates at 100 decisions)


def test_crash_penalty_and_reset():
    st = EnvSettings(mode="solo", n=2, out_dim=2, reward=RewardWeights(crash=-20.0), seed=3)
    sim = LabSim(st, REGS)
    sim.reset()
    hard_left = np.array([[1.0, 1.0], [1.0, 1.0]])
    total = np.zeros(2)
    for _ in range(400):
        obs, r, term, trunc, infos = sim.step(hard_left)
        total += r
        if term.any():           # the two cars hit the wall at different decisions
            break
    assert term.any()
    i = int(np.nonzero(term)[0][0])
    assert infos[i]["episode"]["reason"] == "wall"
    assert r[i] < -19.0
    assert sim.arenas[0].ep_len[i] == 0        # restarted


def test_gym_and_pettingzoo_envs():
    env = RaicingEnv(EnvSettings(mode="solo", out_dim=2))
    obs, info = env.reset(seed=1)
    assert obs.shape == (OBS_DIM,)
    obs, r, term, trunc, info = env.step(np.array([0.0, 0.3], dtype=np.float32))
    assert obs.shape == (OBS_DIM,) and isinstance(r, float)
    pz = RaicingParallelEnv(2, EnvSettings(mode="traffic", out_dim=2, opponents=["pro"], laps=1))
    obs, infos = pz.reset(seed=2)
    assert set(obs) == {"car_0", "car_1"}
    obs, rew, term, trunc, infos = pz.step({a: np.array([0.0, 0.2]) for a in pz.agents})
    assert set(rew) == {"car_0", "car_1"}


def test_full_actions_matches_brain_act():
    b = mlp_brain([8], out_dim=2, rng=np.random.default_rng(1))
    obs = np.random.default_rng(2).standard_normal((3, OBS_DIM)).astype(np.float32)
    y, _ = b.forward(obs)
    a, _ = b.act(obs)
    assert np.allclose(full_actions(y, 2), a)
    assert full_actions(np.zeros((1, 11)), 11).shape == (1, ACT_DIM)


# ---------------------------------------------------------------------- normalisation
def test_obs_norm_masks_dead_inputs():
    st = ObsStats()
    x = np.random.default_rng(0).standard_normal((500, OBS_DIM))
    x[:, 10] = 3.0                      # never varies
    st.update(x)
    std = st.std()
    assert std[10] > 1e5 and abs(std[0] - 1.0) < 0.2
    z = st.normalize(np.where(np.arange(OBS_DIM) == 10, 50.0, 0.0)[None, :])
    assert abs(z[0, 10]) < 1e-3          # a dead input coming alive is ignored


# ---------------------------------------------------------------------- exporters
def _vec(n=4, out=2):
    cfg = _cfg(envs=n, architecture={"hidden": [32, 24], "outputs": out})
    venv = LabVecNormalize(LabVecEnv(LabSim(settings_from_config(cfg), REGS, QuotaCounter(10_000))))
    obs = venv.reset()
    for _ in range(30):
        obs, *_ = venv.step(np.random.default_rng(0).uniform(-0.3, 0.3, (n, out)).astype(np.float32))
    return venv


def _raw_obs(venv, k=64):
    rng = np.random.default_rng(5)
    base = venv.get_original_obs()
    return (np.repeat(base, k // len(base), axis=0) + rng.normal(0, 0.05, (k, OBS_DIM))).astype(np.float32)


@pytest.mark.parametrize("method", ["ppo", "sac", "td3"])
def test_sb3_export_matches_torch(method):
    from stable_baselines3 import PPO, SAC, TD3
    venv = _vec()
    pk = {"net_arch": dict(pi=[32, 24], vf=[32, 24]) if method == "ppo" else dict(pi=[32, 24], qf=[32, 24]),
          "activation_fn": GeluTanh if method == "sac" else nn.Tanh}
    if method == "ppo":
        model = PPO("MlpPolicy", venv, n_steps=8, batch_size=32, policy_kwargs=pk, device="cpu", seed=0)
        _random_weights_(model.policy)
        brain = export_ppo(model, venv.stats())
    elif method == "sac":
        model = SAC("MlpPolicy", venv, learning_starts=0, buffer_size=1000, policy_kwargs=pk, device="cpu", seed=0)
        _random_weights_(model.actor)
        brain = export_sac(model, venv.stats())
    else:
        model = TD3("MlpPolicy", venv, learning_starts=0, buffer_size=1000, policy_kwargs=pk, device="cpu", seed=0)
        _random_weights_(model.actor)
        brain = export_td3(model, venv.stats())
    brain.validate()
    raw = _raw_obs(venv)
    expected, _ = model.predict(venv.normalize_obs(raw), deterministic=True)
    got, _ = brain.forward(raw)
    assert np.max(np.abs(got - expected)) < 1e-4
    assert brain.spec["output"]["squash"] == ("clip" if method == "ppo" else "tanh")


def test_bc_mlp_export_matches_torch():
    from raicing.lab.algos.bc import build_mlp
    st = ObsStats()
    st.update(np.random.default_rng(1).standard_normal((300, OBS_DIM)) * 2 + 1)
    for act, ln in (("relu", False), ("gelu", True), ("elu", False), ("silu", True)):
        net = build_mlp([24, 16], act, 11, layernorm=ln)
        _random_weights_(net, seed=3)
        brain = export_sequential(list(net), "tanh", (st.mean, st.var, st.count))
        x = np.random.default_rng(2).standard_normal((50, OBS_DIM)).astype(np.float32)
        with torch.no_grad():
            ref = torch.tanh(net(torch.as_tensor(st.normalize(x)))).numpy()
        got, _ = brain.forward(x)
        assert np.max(np.abs(got - ref)) < 1e-4, act
        assert parse_mlp(brain)["layernorm"] == ln


# ---------------------------------------------------------------------- ES
def test_es_population_forward_matches_brain():
    from raicing.lab.algos.es import PopulationPolicy, param_keys
    for gru, ln in ((0, False), (12, True)):
        b = mlp_brain([20], out_dim=3, rng=np.random.default_rng(0), gru=gru, layernorm=ln)
        keys = param_keys(b)
        pol = PopulationPolicy(b, keys)
        rng = np.random.default_rng(1)
        thetas = (np.concatenate([b.weights[k].ravel() for k in keys])[None, :]
                  + rng.normal(0, 0.1, (4, pol.dim))).astype(np.float32)
        W = pol.unpack(thetas)
        x = rng.standard_normal((4, OBS_DIM)).astype(np.float32)
        state = pol.init_state(4)
        for _ in range(3):          # several steps: exercises the GRU memory
            y, state = pol.forward(x, W, state)
        for p in range(4):
            bp = Brain(dict(b.spec), {k: W[k][p] for k in keys})
            s = bp.init_state(1)
            for _ in range(3):
                yp, s = bp.forward(x[p:p + 1], s)
            assert np.max(np.abs(yp[0] - y[p])) < 1e-4


def test_es_one_generation(tmp_path):
    cfg = {"method": "es", "steps": 8 * 60, "envs": 8, "architecture": {"hidden": [16], "outputs": 2},
           "hyperparams": {"population": 8, "episode_decisions": 60}}
    res = run_job(cfg, REGS, None, 10_000, tmp_path, evaluate=False)
    assert res.log["generations"] == 1 and res.steps_used <= 480
    assert res.brain.spec["obs_norm"]
    # continue from it (flat parameters)
    res2 = run_job({**cfg, "init_from": "g1"}, REGS, res.brain, 10_000, tmp_path / "g2", evaluate=False)
    assert res2.brain.meta["parent"] == "g1"


# ---------------------------------------------------------------------- end to end
@pytest.mark.parametrize("method, hp", [
    ("ppo", {"n_steps": 16, "batch_size": 64, "n_epochs": 1}),
    ("sac", {"learning_starts": 64, "gradient_steps": 1, "batch_size": 64}),
    ("td3", {"learning_starts": 64, "gradient_steps": 1, "batch_size": 64}),
    ("bc", {"iterations": 2, "epochs": 1}),
])
def test_run_job_end_to_end(tmp_path, method, hp):
    cfg = {"driver_id": "t-1", "method": method, "steps": 400, "envs": 4, "seed": 1,
           "architecture": {"hidden": [16, 16], "outputs": 2}, "hyperparams": hp}
    seen = []
    res = run_job(cfg, REGS, None, 10_000, tmp_path, progress=seen.append, evaluate=method == "ppo")
    assert isinstance(res, JobResult)
    assert 0 < res.steps_used <= 400
    b = Brain.load(tmp_path)
    b.validate()
    meta = b.meta
    for key in ("method", "steps", "created", "parent", "config_hash", "architecture", "training_curve", "eval"):
        assert key in meta
    assert meta["method"] == method and meta["steps"] == res.steps_used
    assert b.spec["obs_norm"]["mean"] in b.weights
    if method == "ppo":
        assert [t["track"] for t in res.eval["tracks"]] == ["lab:0", "lab:1"]
    assert (tmp_path / "train.log").exists() and (tmp_path / "log.json").exists()
    # continue training from the exported generation (same architecture)
    res2 = run_job({**cfg, "init_from": "t-1@1", "steps": 200}, REGS, res.brain, 10_000, tmp_path / "next",
                   evaluate=False)
    assert res2.brain.meta["parent"] == "t-1@1"


def test_init_from_architecture_mismatch(tmp_path):
    b = mlp_brain([8], out_dim=2, squash="clip")
    with pytest.raises(ValueError, match="architecture incompatible"):
        run_job({"method": "ppo", "steps": 100, "envs": 2, "init_from": "x",
                 "architecture": {"hidden": [16], "outputs": 2}}, REGS, b, 1000, tmp_path, evaluate=False)
    with pytest.raises(ValueError, match="pas été fourni"):
        run_job({"method": "ppo", "steps": 100, "init_from": "x"}, REGS, None, 1000, tmp_path, evaluate=False)


def test_ppo_init_from_reproduces_parent(tmp_path):
    """Loading a brain into the PPO policy keeps its deterministic action (before any update)."""
    from stable_baselines3 import PPO
    from raicing.lab.export import load_into_modules
    parent = mlp_brain([16, 16], out_dim=2, squash="clip", rng=np.random.default_rng(4))
    venv = _vec(n=2)
    model = PPO("MlpPolicy", venv, n_steps=8, batch_size=16, device="cpu",
                policy_kwargs={"net_arch": dict(pi=[16, 16], vf=[16, 16]), "activation_fn": nn.Tanh})
    pol = model.policy
    load_into_modules(parent, list(pol.mlp_extractor.policy_net) + [pol.action_net])
    x = np.random.default_rng(0).standard_normal((10, OBS_DIM)).astype(np.float32)
    a, _ = model.predict(x, deterministic=True)
    p, _ = parent.forward(x)
    assert np.max(np.abs(a - p)) < 1e-5


def test_traffic_with_frozen_brain_opponent(tmp_path):
    opp = mlp_brain([8], out_dim=2, rng=np.random.default_rng(9))
    cfg = {"method": "ppo", "steps": 64, "envs": 2, "architecture": {"hidden": [8], "outputs": 2},
           "hyperparams": {"n_steps": 8, "batch_size": 16, "n_epochs": 1},
           "curriculum": {"mode": "traffic", "opponents": ["pro", "frozen-7"], "laps": 1, "learners_per_world": 2}}
    res = run_job(cfg, REGS, None, 1000, tmp_path, evaluate=False,
                  brain_resolver=lambda bid: opp if bid == "frozen-7" else None)
    assert res.steps_used <= 64
    with pytest.raises(ValueError, match="adversaire inconnu"):
        run_job(cfg, REGS, None, 1000, tmp_path / "x", evaluate=False)


def test_export_std_rule():
    var = np.array([0.0, 1e-8, 1e-4, 4.0])
    std = export_std(var)
    assert std[0] > 1e5 and std[1] > 1e5
    assert np.isclose(std[2], 0.05) and np.isclose(std[3], 2.0)
    x = np.array([[1.0, 1.0, 1.0, 1.0]], dtype=np.float32)
    assert normalize(x, np.zeros(4, np.float32), std).shape == (1, 4)
