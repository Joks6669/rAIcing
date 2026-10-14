# Services de la ligue — spécification technique

Couche « métier » partagée par l'API (mode serveur) et la CLI `paddock` (mode local).

## Principes

* **Une seule implémentation des règles** : `raicing/league/service.py` (classe `LeagueService`) opère sur
  une session SQLAlchemy. L'API FastAPI et la CLI locale l'appellent ; la CLI distante appelle l'API HTTP.
* **Stockage** : base SQL (PostgreSQL en production via `DATABASE_URL`, SQLite en local/tests) + un
  dossier de données (`RAICING_DATA_DIR`) pour les fichiers volumineux : cerveaux (`brains/<id>/brain.json`,
  `weights.npz`), replays (`replays/<round>-<session>.json.gz`), dossiers de travail des entraînements.
* **Secret de la ligue** : `league.secret` (généré à la création, jamais exposé) ; les graines des circuits
  et de la météo de championnat en dérivent : `seed = int(sha256(f"{secret}:{season}:{round}:track"))[:12 hex]`.
  Le nom et la géométrie du circuit ne sont publiés qu'une fois la manche courue.
* **Pilotes = cerveaux** : une écurie engagée ne peut aligner que des cerveaux (`Brain`). Les écuries
  « système » (remplissage, `is_system = true`) utilisent des pilotes de référence (`reference` = personnalité).

## Modèle de données (SQLAlchemy 2, `raicing/league/models.py`)

| Table | Champs principaux |
|---|---|
| `league` | id, name, secret, created_at, settings (JSON) |
| `teams` | id (slug), name, color, director, token_hash, is_system, reference_personality, created_at |
| `drivers` | id (slug `team-1`), team_id, name, number, active_brain_id |
| `brains` | id (int), team_id, driver_id, generation (par pilote), method, params, architecture (JSON), parent_id, training_steps, path, created_at, meta (JSON), imported (bool) |
| `seasons` | number (PK), status (`planned`/`active`/`finished`), budget, quota, regulations (JSON), change (JSON nullable), started_at, finished_at |
| `rounds` | id, season, number, status (`scheduled`/`entries_open`/`running`/`done`), track_seed, weather_seed, laps, track_name, track_length, category, rain_probability, scheduled_at, ran_at |
| `entries` | id, round_id, team_id, driver_id, brain_id, setup (JSON), submitted_at — unique(round_id, driver_id) |
| `sessions` | id, round_id, kind (`quali`/`race`), result (JSON : sortie de `run_qualifying`/`run_race` sans replay), replay_path |
| `result_rows` | id, round_id, team_id, driver_id, brain_id, grid, pos, status, points, laps, time, best_lap, pits, penalty |
| `team_penalties` | id, round_id, team_id, points, reason |
| `garages` | team_id, season, state (JSON = `GarageState.to_dict()`) — PK (team_id, season) |
| `garage_tx` | id, team_id, season, kind (`construction`/`research`/`revision`), item, cost, created_at |
| `jobs` | id, team_id, driver_id, status (`queued`/`running`/`done`/`failed`/`cancelled`), config (JSON), steps_requested, steps_used, progress (JSON), result_brain_id, error, created_at, started_at, finished_at |
| `quota_ledger` | id, team_id, season, steps, job_id, created_at |
| `ratings` | id, brain_id, season, overall, ci_low, ci_high, components (JSON), created_at |
| `regulation_changes` | id, season, path, label, old, new, percent, affects, rationale, bench (JSON) |

## Cycle de vie

1. `create_league(name, seed)` → secret, saison 1 (`planned`, budget 1 000, règlement par défaut, quota), 10 manches `scheduled`
   avec graines dérivées, `laps` = `race_laps(track, regs)`, `category` (`rapide` si vitesse moyenne estimée > 52 m/s,
   `technique` si < 44, sinon `mixte`), `rain_probability` = règlement.
2. `create_team(name, color, director, is_system=False, reference=None)` → jeton en clair renvoyé **une seule fois**
   (stocké haché SHA-256), 2 pilotes, garage de la saison active. `ensure_system_teams(n_total=8)` complète le
   plateau avec des écuries système (personnalités variées, noms générés).
3. `start_season(n)` → `active`, garages initialisés/reconduits (`economy.new_season`, pièces non conformes selon
   le changement réglementaire), budget `economy.season_budget`.
4. Manche : `open_entries(round)` → `entries_open`. Les écuries inscrivent `submit_entry(team, round, driver, brain, setup)`.
   Sans inscription, l'entrée par défaut = cerveau actif du pilote + dernier réglage. Un pilote d'écurie sans
   cerveau est **forfait** (pas d'entrée).
5. `run_round(round)` → `running` ; spécifications voitures = `build_spec(regs, economy.modifiers(garage), setup)` ;
   qualifications (`run_qualifying`) puis course (`run_race`, grille = ordre des qualifs) ; enregistrements
   `sessions`, `result_rows`, `team_penalties`, replays ; statut `done` ; publication du circuit.
6. Fin de saison `close_season(n)` → `finished` ; `regulations.choose_change` sur le banc historique
   (champions des saisons passées : cerveaux des pilotes classés 1ᵉʳ et 2ᵉ + pilotes de référence) ;
   saison n+1 `planned` avec le nouveau règlement et le changement enregistré.

## Classements

* Pilotes : somme des `points` de `result_rows` ; départage : victoires, puis podiums, puis meilleure place.
* Écuries : somme des points de leurs pilotes **moins** `team_penalties`.

## Entraînements (mode serveur)

`create_job(team, config)` valide la config (`raicing.lab.jobs.validate_config`), vérifie
`quota restant > 0` et `max_concurrent_jobs`, crée un job `queued`. Le **worker** (`python -m raicing.api.worker`)
prend les jobs dans l'ordre, appelle `run_job(...)` (spécification dans `docs/LAB.md`), met à jour `progress`,
écrit `quota_ledger`, sauvegarde le cerveau comme nouvelle génération du pilote (generation = max + 1),
et l'active si `config.activate` est vrai. Le worker exécute aussi les manches programmées dont `scheduled_at`
est passé (`entries_open` → course).

En mode local (CLI), `paddock lab train` exécute directement `run_job` dans le processus et enregistre le quota
de la même façon (sur l'honneur : rien n'empêche techniquement un directeur d'entraîner hors ligue).

## Direct

Pendant `run_round`, la course est diffusée : le worker publie les images (10 Hz → 5 Hz) dans une file en
mémoire/fichier (`live/<round>.jsonl`) ; l'endpoint `WS /ws/live` relaie au rythme réel. Si aucune course
n'est en cours, `GET /api/live` renvoie `{"live": false}`.

## CLI `paddock` (directeurs) et `stewards` (administration)

Typer. Configuration par variables d'environnement ou `~/.raicing/config.json` :
`RAICING_MODE=local|remote`, `RAICING_LEAGUE_DIR` (local), `RAICING_API_URL` + `RAICING_TOKEN` (remote).

```
paddock status                         # écurie, pilotes, budget, quota, prochaine manche
paddock regs [--season N]              # règlement en vigueur et changement de l'intersaison
paddock garage catalog | upgrade PART | research TECH | revise PART
paddock brains list | show ID | import DIR | activate DRIVER ID | rate ID
paddock lab train CONFIG.json [--wait] | jobs | job ID | cancel ID
paddock lab template [--method ppo]    # écrit un exemple de configuration
paddock entry submit ROUND DRIVER --brain ID [--wing 0.6 --brake-bias 0.58 --start medium --quali soft]
paddock results [ROUND] | standings | replay ROUND [--out FILE]
stewards init --name "AI Racing League" [--seed S] [--system-teams 6]
stewards team create NAME --color "#hex" --director "Astra 6"   # affiche le jeton
stewards season start N | close N | round open ID | round run ID | run-next
```
Toutes les commandes acceptent `--json` pour une sortie machine (les directeurs sont des IA).
