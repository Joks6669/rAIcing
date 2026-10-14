# CLAUDE.md — rAIcing / AI Racing League

Contexte de reprise pour une session Claude Code sans historique. **Lire d'abord `docs/HANDOVER.md`** (passation complète et détaillée). Travailler sur la branche `main` (la branche `claude/focused-galileo-jdvt9w` est un squelette générique sans rapport, sans ancêtre commun). Propriétaire : Gabriel Meunier
(francophone ; interface et docs en français, code et commentaires en anglais).

## Concept

Un championnat de course 2D où **des IA dirigent des écuries** (ex. agents LLM « Astra 6 », « Opus 5.5 »).
Chaque écurie : 2 voitures, 2 pilotes qui sont des **réseaux de neurones** entraînés par l'IA directrice.
L'IA directrice construit la voiture (budget de points), entraîne les pilotes (quota d'interactions),
inscrit ses pilotes aux manches. **Aucune intervention pendant la course** (seuls les deux pilotes
communiquent, via un canal radio numérique libre). L'humain observe (site web).

Principe : *les IA dirigent, construisent, apprennent, communiquent et pilotent ; le système définit et
applique les règles ; l'humain observe.*

## Décisions de game design (validées avec Gabriel)

Spécification complète : `docs/SPEC.md` (V1.2). Toutes les valeurs numériques : `raicing/config.py`
(`Regulations`), qui fait foi.

- Saison de 10 manches sur circuits procéduraux inédits (graines secrètes dérivées du secret de ligue,
  circuit révélé après la course). 10–15 tours selon la longueur. Qualifs : 1 tour lancé, voitures seules.
- Pas de carburant, pas de safety car. Pneus tendre/medium/dur/pluie, arrêts libres et illimités.
- **Contact avec un rebord = élimination.** Voiture cassée (dégâts ≥ 100 %) ou pneu détruit = élimination.
- **Contacts entre voitures : règle de responsabilité** (choix de Gabriel). Fautif = voiture qui arrive
  par l'arrière (sauf freinage injustifié de celle de devant) ou qui se déplace latéralement vers l'autre.
  Contact fautif : +5 s. Si la victime est éliminée dans les 3 s : fautif disqualifié et **écurie −15 pts**
  (le sacrifice d'un coéquipier ne paie jamais).
- Barème 25-18-15-12-10-8-6-4-2-1 ; écurie = somme de ses deux voitures moins les pénalités.
- Économie : budget identique pour tous (1 000 pts en S1, puis 850–1 150), pas de revenus, report ≤ 20 %.
  7 pièces niveaux 0–10 à rendements décroissants, coût ×1,35/niveau, capacités (encombrement, thermique,
  masse) et compatibilités, technologies, révisions après changement de règlement (`raicing/league/economy.py`).
- Un changement de règlement par intersaison (5–10 %, 15 % max), validé par un banc historique
  (`raicing/league/regulations.py`).
- **Pilotes = cerveaux déclaratifs** (architecture JSON + poids npz, exécutés en numpy, aucun code
  arbitraire) : sûr côté serveur, déterministe. Pas de pilotes scriptés pour les écuries engagées
  (les pilotes de référence scriptés servent aux écuries système, à l'équilibrage, à l'imitation et au rating).
- Quota d'entraînement identique par saison ; en mode local il est **sur l'honneur** (accepté par Gabriel).
- Driver Rating purement informatif (6 composantes, normalisé vs pilote de référence « pro »).
- Choix techniques : moteur **Python + numba** (pas de Rust : mesuré suffisant, portable Windows) ;
  site **Next.js complet à héberger** (choix de Gabriel) ; API FastAPI + PostgreSQL ; directeurs IA via
  **CLI `paddock`** (choix de Gabriel).

## Architecture du dépôt

```
raicing/
  config.py            Regulations (tous les paramètres), sérialisation, accès par chemin "car.power"
  track/               génération procédurale (generator.py), géométrie + projection (geometry.py)
  physics/kernels.py   noyaux numba : dynamique bicyclette, pneus (Pacejka simplifié, ellipse de friction,
                       sensibilité à la charge, transferts), température/usure, projection piste, rebords
  physics/car.py       CarSpec, Setup (aileron, répartition freinage, gommes), PerformanceModifiers
  world.py             World : état packé (S, PRM), laps, stands, collisions + commissaires, éliminations,
                       classement, enregistrement replay. 100 Hz physique, décisions à 20 Hz.
  weather.py           script de pluie + carte d'humidité par cellule de 10 m
  sensors.py           observations des pilotes (OBS_DIM = 157, voir OBS_LAYOUT)
  brains/spec.py       format cerveau "raicing-brain/1", inférence numpy, mlp_brain, (dé)sérialisation
  drivers/             ligne de course + pilotes de référence (personnalités : debutant, rapide, prudent,
                       pneus, cooperatif, pro)
  race.py              run_qualifying, run_race, classification, points, replays (raicing-replay/1)
  lab/                 évaluation (run_solo, run_traffic ; lab_track = graines 10M+, validation_track = 20M+)
                       + entraînement (voir état ci-dessous)
  league/              economy, regulations, rating (+ models/db/service/training/live : voir état)
  api/                 FastAPI app, worker, settings (voir état)
  cli/                 paddock (directeurs), stewards (administration) (voir état)
web/                   site Next.js (voir état) ; web/fixtures = vraie course d'exemple + replay
docs/                  SPEC.md, API.md (contrat HTTP), LAB.md (entraînement), LEAGUE.md (services, CLI)
tests/  scripts/  demo_brains/
```

Installation : `pip install -e ".[lab,api,dev]"` + `pip install numba` (numba recommandé ; sans lui
le code tourne en Python pur, ~50× plus lent). Web : `cd web && npm install && npm run build`.

## État d'avancement (au 14/10/2026)

### Fini et testé manuellement (par scripts, pas encore de suite pytest dédiée)
- Génération de circuits (30 graines : 100 % valides, ~12 ms/circuit).
- Moteur physique numba : déterministe, ~3 700 décisions/s pour 1 voiture (limité par le pilote Python),
  course 16 voitures × 14 tours ≈ 90 s CPU avec pilotes de référence.
- Pilote de référence « pro » : 0 sortie sur 30 circuits ; autres personnalités 0–1/20.
  Course à 16 voitures de référence : ~13 classés sur 16, sans carambolage au départ.
- Pneus calibrés (usure/tour : tendre ~0,11, medium ~0,063, dur ~0,038) ; températures dans les fenêtres.
- Course complète : qualifs, grille, départ arrêté, stands, drapeau à damier, classement, points,
  pénalités, replays (~10 Mo JSON, compressés gzip).
- Économie du garage (smoke test), Driver Rating (testé sur personnalités de référence, ~15 s/rating rapide).

### Commencé, NON vérifié (écrit par des sous-agents interrompus par la limite d'usage)
Commit `WIP: partial lab, league/API/CLI and web work…`. Rien de cela n'a été relu ni testé par la
session principale :
- `raicing/lab/` : envs.py (VecEnv SB3, Gymnasium, PettingZoo), quota, rewards, export, normalize,
  teachers, algos/ (sb3, es, bc), jobs.py (`run_job`, `validate_config`). `scripts/train_demo.py`,
  `scripts/bench_lab.py`. `demo_brains/demo-bc` : un pilote entraîné par imitation du « pro » ;
  `demo_brains/demo-report.json` contient des mesures (à lire, non validées).
- `raicing/league/{models,db,service,training,live,util}.py`, `raicing/api/{app,worker,settings,__main__}.py`,
  `raicing/cli/{paddock,stewards,backend,common}.py`.
- `tests/` : test_lab, test_service, test_api, test_cli, test_live, test_worker, conftest — jamais exécutés
  par la session principale.
- `web/` : app Next.js (pages accueil, saison, course, direct, écuries, pilote, règlement, à propos ;
  composants ReplayViewer, LiveViewer…). Vérifié depuis (voir plus bas).

### Vérifications faites le 14/10/2026 au soir
- `pytest -q` : 65 réussis, 0 échec (3 tests du labo corrigés, dont un vrai défaut dans `algos/es.py`).
- `demo_brains/demo-bc` (imitation du « pro ») : 2 tours sans sortie sur 5/5 circuits de labo et 4/5 de
  validation, à 0–2 % du « pro ». Critère d'acceptation atteint pour BC. RL démontré : `demo-ppo` (BC + PPO 4 M) 20/20 circuits
  de validation, ratio médian 1,011, plus rapide que le « pro » sur 9/20 (`docs/LAB.md`).
- `docs/DIRECTOR_GUIDE.md` rédigé (guide des IA directrices, exemples réels).

### Prochaines étapes (dans cet ordre)
1. ~~`pytest -x -q` vert~~ (fait). Relire service.py / app.py contre `docs/LEAGUE.md` et `docs/API.md`.
2. ~~Laboratoire : vérifier que `run_job` apprend réellement~~ (fait) — critère d'acceptation : un pilote qui
   boucle 2 tours sans sortie sur ≥ 3/5 circuits de labo non vus et ≥ 3/5 `validation_track(0..4)`,
   temps comparés au « pro ». Mesurer le débit (décisions/s) par méthode.
3. ~~Web : build, mode mock, branchement API réelle, captures desktop/mobile~~ (fait).
4. ~~Rédiger `docs/DIRECTOR_GUIDE.md`~~ (fait).
5. ~~Équilibrage~~ (fait, `docs/BALANCE.md` ; freins = anti-blocage, attrition ~30 % acceptée).
6. ~~Saison de démonstration~~ → remplacée par la page **Rencontres** (décision de Gabriel : l'entraînement
   se fait ailleurs ; on dépose les modèles entraînés). Voir `docs/RENCONTRES.md`, `raicing/matches.py`.
7. Déploiement : docker-compose (postgres, api, worker, web), CI GitHub Actions, hébergement
   (Gabriel a connecté Vercel pour le site ; l'API + worker nécessitent un serveur à part).

## Pièges connus
- Le premier appel au moteur compile les noyaux numba (quelques secondes, mis en cache ensuite).
- `World.idx` est une propriété qui renvoie une copie : ne pas écrire dedans (utiliser `place_car`).
- Les vues `world.x`, `world.vx`… pointent dans `world.S` : affectation par élément OK, jamais de
  réaffectation de l'attribut entier hors des setters fournis.
- En session `lab`, ni arrêt prolongé ni demi-tour n'éliminent : tronquer les épisodes soi-même.
- Dans un même World `race`, le premier arrivé déclenche le drapeau pour tous : pour des courses
  indépendantes en entraînement, utiliser `lab` + comptage de tours maison.
- Un rapport de sous-agent a corrigé 2 bugs moteur : `sensors.py` (moins de 6 adversaires) et
  `weather.py` (sessions courtes).
- Dépôt distant : `https://github.com/Joks6669/rAIcing` (branche `main`).
