# Laboratoire d'entraînement — spécification technique

Le laboratoire transforme une **configuration d'entraînement** (choisie par l'IA directrice) en une
nouvelle **génération de cerveau** (`raicing.brains.spec.Brain`), en consommant un nombre compté
d'interactions (décisions de pilote dans un simulateur d'entraînement).

## Point d'entrée unique

```python
from raicing.lab.jobs import run_job, JobResult, QuotaExceeded

result: JobResult = run_job(
    config: dict,                 # voir schéma ci-dessous (validé par raicing.lab.jobs.validate_config)
    regs: Regulations,            # règlement de la saison en cours
    init_brain: Brain | None,     # génération de départ (config["init_from"]) ou None
    quota_remaining: int,         # interactions encore disponibles pour l'écurie
    workdir: Path,                # dossier de travail (checkpoints, logs)
    progress: Callable[[dict], None] | None = None,   # appelé régulièrement {"steps": ..., "mean_reward": ..., ...}
    should_stop: Callable[[], bool] | None = None,    # annulation coopérative
)
# JobResult: brain (Brain validé, meta remplies), steps_used (int ≤ min(config.steps, quota_remaining)),
#            log (dict: courbe d'apprentissage, durée, hyperparamètres effectifs), eval (dict: tours d'évaluation)
```

Le quota est compté **à la source** par l'environnement (`QuotaCounter`) : chaque décision d'une voiture
contrôlée par l'apprenant = 1 interaction. Les adversaires (pilotes de référence, cerveaux figés) ne
comptent pas. Les démonstrations d'imitation comptent (une décision du professeur = 1 interaction).
L'entraînement s'arrête proprement dès que `steps` ou `quota_remaining` est atteint.

## Schéma de configuration

```json
{
  "driver_id": "astra-1",
  "method": "ppo",               // ppo | sac | td3 | es | bc
  "steps": 2000000,
  "init_from": null,             // id de génération : poursuit l'entraînement si l'architecture est compatible
  "architecture": {"hidden": [256, 256], "activation": "tanh", "gru": 0, "layernorm": false, "outputs": 11},
  "hyperparams": {},             // spécifiques à la méthode (lr, gamma, batch_size, n_steps, ent_coef, sigma, population...)
  "reward": {
    "progress": 1.0,             // par 100 m parcourus
    "time": -0.02,               // par décision
    "crash": -20.0,              // élimination (mur, casse, pneu détruit)
    "wear": -1.0,                // par unité d'usure de pneu consommée
    "smooth": 0.0,               // pénalité sur |Δbraquage|
    "edge": 0.0,                 // pénalité quand la voiture s'approche à moins de 1 m du rebord
    "finish": 10.0,              // course terminée (mode race / traffic)
    "position": 0.0,             // par place gagnée (mode traffic)
    "contact": -5.0              // contact fautif (mode traffic)
  },
  "curriculum": {
    "mode": "solo",              // solo | race | traffic
    "laps": 3,                   // longueur d'un épisode (race/traffic)
    "opponents": ["pro", "prudent"],   // personnalités de référence ou ids de cerveaux figés (traffic)
    "rain_fraction": 0.2,        // part des circuits d'entraînement mouillés
    "tyre_randomization": true,  // gomme, usure et température aléatoires au départ (mode solo)
    "track_rotation": 4000       // décisions entre deux changements de circuit
  },
  "envs": 64,                    // voitures entraînées en parallèle
  "seed": 0
}
```

* `solo` : voitures fantômes indépendantes sur un circuit d'entraînement (pas de collisions), départs
  lancés à des positions aléatoires ; épisode = jusqu'à l'élimination ou `episode_decisions` (1 tour ≈ 1 200–2 000).
* `race` : course indépendante de `laps` tours par voiture (arrêts aux stands possibles, gestion des pneus).
* `traffic` : vraie course avec collisions contre des adversaires ; l'apprenant contrôle 1 ou 2 voitures
  (coéquipiers, canal radio actif).

Circuits d'entraînement : `raicing.lab.evaluation.lab_track(k)` (graines publiques `10 000 000 + k`).
Les circuits de validation (`20 000 000 + k`) et de championnat (graines secrètes) ne doivent jamais servir
à l'entraînement.

## Méthodes intégrées

| Méthode | Implémentation | Export |
|---|---|---|
| `ppo` | stable-baselines3 PPO sur un `VecEnv` vectorisé maison | `policy.mlp_extractor.policy_net` + `action_net`, sortie `clip` |
| `sac` | stable-baselines3 SAC | `actor.latent_pi` + `actor.mu`, sortie `tanh` |
| `td3` | stable-baselines3 TD3 | `actor.mu`, sortie `tanh` |
| `es` | stratégies d'évolution (OpenAI-ES, numpy) — la population est évaluée en parallèle dans le même monde | poids directs (MLP ou GRU) |
| `bc` | clonage de comportement d'un pilote de référence (PyTorch) | MLP |

La normalisation des observations (moyenne/écart-type courants) est exportée dans `obs_norm` du cerveau.

## Fichiers

* `raicing/lab/envs.py` : `LabVecEnv` (SB3 `VecEnv`), `RaicingEnv` (Gymnasium, 1 voiture), `RaicingParallelEnv` (PettingZoo).
* `raicing/lab/rewards.py`, `raicing/lab/quota.py`, `raicing/lab/export.py`, `raicing/lab/algos/{sb3,es,bc}.py`, `raicing/lab/jobs.py`.
