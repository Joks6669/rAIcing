# Rencontres — faire courir des modèles entraînés ailleurs

La page **Rencontres** du site organise des courses d'exhibition entre pilotes entraînés n'importe où.
On y dépose le fichier de chaque modèle, on choisit le circuit, le nombre de tours et la météo, et le
serveur simule les qualifications puis la course. Le replay s'affiche avec la réalisation télé.

Les rencontres sont indépendantes du championnat : pas de quota, pas de garage (toutes les voitures sont
la voiture de base du règlement en vigueur).

## 1. Lancer l'interface sur son ordinateur

Prérequis : Python 3.10+ et Node.js 20+.

```bash
pip install -e ".[api]"
pip install numba            # fortement recommandé (simulation ~50× plus rapide)
python scripts/rencontres.py
```

Le premier lancement construit le site (quelques minutes). Le navigateur s'ouvre ensuite sur
`http://localhost:3000/rencontres`. `Ctrl+C` arrête tout. Les rencontres, les modèles et les replays sont
conservés dans `rencontres_data/`.

Options : `--port 3000` (site), `--api-port 8000` (simulation), `--data DOSSIER`, `--rebuild`.

## 2. Le fichier à déposer

Un **.zip** contenant deux fichiers :

| Fichier | Contenu |
|---|---|
| `brain.json` | l'architecture du réseau (format `raicing-brain/1`) |
| `weights.npz` | les poids (tableaux float32 nommés) |

Ce format est déclaratif : le serveur exécute le réseau lui-même, en numpy, sans jamais exécuter de code
venu du fichier. Couches possibles : `linear`, `relu`, `tanh`, `gelu`, `sigmoid`, `elu`, `silu`,
`layernorm`, `gru`, `residual`, normalisation des entrées `obs_norm`. Au plus 2 millions de paramètres.

Le site vérifie le fichier au dépôt et explique le problème s'il est refusé (zip illisible, fichiers
manquants, mauvais nombre d'entrées…).

## 3. Ce que le réseau doit lire et produire

- **Entrée : 157 valeurs** à chaque décision (20 par seconde) : la voiture, la piste devant elle
  (16 points jusqu'à 270 m), la météo, la course, les 6 adversaires les plus proches, le coéquipier.
  Détail : `docs/DIRECTOR_GUIDE.md` § 3 et `raicing/sensors.py`.
- **Sortie : 2 à 11 valeurs** dans [−1, 1] : braquage, pédale (−1 frein … +1 gaz), puis demande d'arrêt,
  4 scores de gomme et 4 valeurs de radio.

Un modèle à **2 sorties ne s'arrête jamais aux stands** : il part en pneus durs (choix « Gomme auto »),
qui tiennent une course entière sur le sec. Il en faut 7 ou plus pour gérer les arrêts.

## 4. Entraîner ailleurs, mais sur ce simulateur

Un pilote n'apprend à conduire que la physique sur laquelle il s'entraîne : il faut donc entraîner **avec
le simulateur du projet**, qui s'installe sur n'importe quelle machine (`pip install -e ".[lab]"`).
Il fournit un environnement Gymnasium standard, utilisable avec la bibliothèque de son choix :

```python
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from raicing.lab.envs import EnvSettings, RaicingEnv

def make():
    return RaicingEnv(EnvSettings(mode="solo", out_dim=2))   # mode "traffic" pour apprendre le peloton

env = VecNormalize(DummyVecEnv([make] * 4), norm_reward=False)
model = PPO("MlpPolicy", env, n_steps=256)
model.learn(total_timesteps=4_000_000)
model.save("mon-pilote.zip")
env.save("vec_normalize.pkl")
```

`EnvSettings` règle le curriculum : `mode` (`solo`, `race`, `traffic`), `out_dim` (2 à 11),
`opponents`, `rain_fraction`, `laps`… (voir `docs/LAB.md`). Pour beaucoup de voitures en parallèle,
`raicing.lab.envs.LabVecEnv` est un `VecEnv` stable-baselines3 bien plus rapide.

Ce qui marche (mesuré, `docs/LAB.md`) : imiter d'abord le pilote de référence, puis affiner par
renforcement. Un pilote entraîné **seul en piste** (`solo`) sort vite en peloton : pour les rencontres,
terminer par du `traffic`.

## 5. Convertir le modèle

```bash
# stable-baselines3 (PPO, SAC ou TD3), avec la normalisation des observations :
python scripts/convert_model.py ppo mon-pilote.zip --vecnormalize vec_normalize.pkl -o pilote-pour-rencontre.zip

# réseau PyTorch nn.Sequential sauvegardé avec torch.save(net, "pilote.pt") :
python scripts/convert_model.py torch pilote.pt --squash tanh -o pilote-pour-rencontre.zip
```

Le convertisseur vérifie le nombre d'entrées, essaie le réseau sur une observation et affiche son nombre de
sorties. Le fichier produit se dépose tel quel sur la page Rencontres.

## 6. API (pour automatiser)

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/api/matches` | crée une rencontre (JSON ci-dessous), renvoie son `id` |
| `GET` | `/api/matches` | dernières rencontres |
| `GET` | `/api/matches/{id}` | statut (`queued`, `running`, `done`, `failed`), progression, résultats |
| `GET` | `/api/matches/{id}/replay?session=race\|quali` | replay `raicing-replay/1` |

```json
{
  "name": "Astra contre Opus",
  "entrants": [
    {"name": "Astra", "color": "#E10600", "start": "auto", "package": "<zip en base64>"},
    {"name": "Opus", "color": "#00A3E0", "package": "<zip en base64>"},
    {"reference": "pro"}
  ],
  "track_seed": null,
  "laps": null,
  "weather": "dry",
  "fill": 4
}
```

`track_seed` : `null` pour un circuit inédit tiré au hasard, ou un entier pour rejouer le même circuit.
`laps` : `null` pour la longueur normale (10 à 15 tours). `weather` : `dry`, `rain` ou `random`.
`fill` : nombre de pilotes de référence ajoutés en fond de grille (20 voitures au plus).
Les rencontres se simulent une par une ; une course complète prend une à deux minutes.
