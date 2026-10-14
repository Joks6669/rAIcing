# Passation complète — rAIcing / AI Racing League

Ce document est destiné à une session Claude (Claude Code ou Cowork) qui reprend le projet sans aucun
contexte. Il explique **quoi**, **pourquoi** et **où en est chaque brique**. Lire ensuite `CLAUDE.md`
(résumé), `docs/SPEC.md` (règles), puis le code.

> Important : si tu as été lancé sur la branche `claude/focused-galileo-jdvt9w`, elle contient un
> squelette générique (Next.js + Prisma + CRUD « items ») créé sans connaître le projet. Elle n'a **aucun
> ancêtre commun** avec `main`. Le vrai projet est sur **`main`**. Travaille à partir de `main` ;
> ce squelette peut être abandonné.

---

## 1. Le propriétaire et la façon de travailler avec lui

- **Gabriel Meunier**, chargé de communication (pas développeur de métier). Projet personnel.
- Il écrit en français, souvent vite (fautes de frappe). Réponds en **français**, de façon **directe,
  concise et argumentée**, sans emojis. Il apprécie qu'on le contredise quand c'est justifié.
- Il délègue les choix techniques mais tranche le game design. Pose une question seulement quand la
  décision lui appartient vraiment (règle de jeu, coût, hébergement) ; sinon décide et dis-le.
- Il utilise Windows. Git a été installé récemment sur son PC ; GitHub Desktop éventuellement.
- Il a connecté les connecteurs GitHub et Vercel à Claude.

## 2. Le concept

Un championnat de course automobile 2D où **les IA sont les écuries** :

- Chaque écurie est dirigée par une **IA directrice** (un agent LLM : « Astra 6 », « Opus 5.5 »…).
- Elle a **2 voitures** et **2 pilotes**. Un pilote est un **réseau de neurones** (« cerveau ») que
  l'IA directrice conçoit et entraîne.
- Entre les courses, l'IA directrice : améliore sa voiture (budget de points), entraîne ses pilotes
  (quota d'interactions avec le simulateur), choisit les réglages et inscrit ses pilotes.
- **Pendant la course, personne n'intervient** : seuls les deux pilotes d'une écurie communiquent, par
  un canal radio numérique (4 valeurs) dont ils inventent eux-mêmes le protocole.
- L'humain regarde (site web : replays, direct, classements, fiches pilotes).

Quatre objectifs fixés par le game design V1.1 : skill cap très élevé (progression sur des dizaines de
saisons), compétition équilibrée (l'intelligence compte plus que la chance ou l'argent), autonomie
totale des IA, architecture durable (ajouter pièces, règles, méthodes sans tout reconstruire).

## 3. Règles du jeu (version 1.2, validée)

Référence : `docs/SPEC.md` ; valeurs : `raicing/config.py` (`Regulations`), qui fait foi.

| Élément | Règle | Où c'est codé |
|---|---|---|
| Saison | 10 manches, circuits procéduraux **inédits**, révélés après la course | `league/service.py` (graines dérivées du secret de ligue) |
| Course | 10 à 15 tours, choisis pour une durée cible ~1 050 s | `race.race_laps` |
| Qualifs | 1 tour de lancement + 1 tour chrono, voitures seules (fantômes), conditions identiques | `race.run_qualifying` |
| Départ | Grille en quinconce, départ arrêté, ordre des qualifs | `World.setup_grid` |
| Carburant / safety car | Aucun | — |
| Pneus | tendre / medium / dur / pluie ; arrêts libres et illimités | `config.CompoundSpec` |
| Élimination | contact avec un **rebord** ; dégâts ≥ 100 % ; usure pneu ≥ 100 % ; arrêt > 20 s ; demi-tour > 3 s | `physics/kernels.step_cars`, `World._handle_flags` |
| Contacts entre voitures | **règle de responsabilité** (choix explicite de Gabriel) | `World._new_incident` |
| Points | 25-18-15-12-10-8-6-4-2-1 ; écurie = somme des deux voitures − pénalités | `race.classify` |
| Fin | quand le leader termine, les autres finissent leur tour ; délai de grâce ensuite | `World._on_lap_cross`, `_post_decision` |

### Règle de responsabilité (cœur de l'équilibre sportif)
Sans elle, la stratégie dominante serait d'envoyer son pilote B pousser le leader adverse dans le mur.
- Contact « par l'arrière » (écart longitudinal > 2,5 m) : fautif = celui qui arrive derrière, **sauf
  freinage injustifié** de celui de devant (freinage fort alors que sa vitesse est < 70 % de la vitesse de
  passage possible sur les 150 m suivants, dans la seconde précédant le contact).
- Contact « côte à côte » : fautif = celui qui se déplace latéralement vers l'autre (écart > 1 m/s), sinon
  incident de course.
- Contact fautif avec vitesse de rapprochement ≥ 3 m/s : **+5 s**.
- Si la victime est éliminée dans les **3 s** : fautif **disqualifié** et **écurie −15 points**.
  Le sacrifice d'un coéquipier ne rapporte donc jamais. S'applique aussi entre coéquipiers.

### Arrêts aux stands (modèle abstrait)
Demande d'arrêt active + passage dans la zone d'entrée (50 m avant la ligne) → la voiture sort de la
piste pendant `pit_lane_time` (15 s) + service (3 s) (+ 6 s de réparation si dégâts > 15 %), sa
progression virtuelle avance pendant ce temps, puis elle ressort 150 m après la ligne à 80 km/h, côté
droit, sans collision pendant 2 s, pneus neufs à 80 °C.

## 4. Le simulateur (fini et testé)

### 4.1 Circuits — `raicing/track/`
- `generator.generate_track(seed)` : points de contrôle aléatoires autour d'un centre (9–16, parfois
  une « encoche » pour une épingle), spline cubique périodique, mise à l'échelle 3,2–5,6 km, lissage
  jusqu'à rayon mini 16 m, largeur 13–19 m variable, test d'auto-proximité (KD-tree), sens aléatoire,
  ligne de départ au milieu de la portion la plus droite (pit lane et grille sur du droit), zones à
  séchage lent (ombre). Nom généré (« Circuit de Valnoue »). ~12 ms par circuit, 100 % de succès sur 30 graines.
- `geometry.Track` : ligne médiane échantillonnée tous les 2 m, normales (gauche = +), courbure signée,
  bords, projection locale autour d'un indice indice-indice (`project`), `speed_profile` quasi statique
  (utilisé par les commissaires et les pilotes de référence, pas par la physique).
- Espaces de graines : **labo** `10 000 000 + k` (entraînement autorisé), **validation**
  `20 000 000 + k` (Driver Rating, banc réglementaire — ne jamais entraîner dessus), **championnat**
  graines secrètes dérivées de `league.secret`.

### 4.2 Physique — `raicing/physics/kernels.py` (numba) + `raicing/world.py`
- Pas fixe **1/100 s** ; décisions des pilotes à **20 Hz** (`decision_every = 5`). Le rendu n'intervient
  jamais : un replay accéléré est identique.
- **Modèle bicyclette dynamique** : masse 760 kg, inertie, empattement 3,4 m, CG à 1,75 m de l'avant,
  hauteur 0,28 m. Fusion vers un modèle cinématique sous 5–10 m/s (stabilité numérique).
- **Pneus** : formule « magique » simplifiée (B=12, C=1,55 ; pic vers 7° de dérive, puis décrochage) ;
  pneus arrière plus larges (grip ×1,12, rigidité ×1,15 → la voiture sous-vire légèrement et reste
  pilotable) ; **ellipse de friction** (frein/accélération réduisent la capacité latérale) ; au-delà de la
  limite longitudinale la roue patine/bloque (95 % de la force, peu de latéral) ; **sensibilité à la
  charge** ; **transferts de masse** longitudinaux et latéraux (avec retard filtré).
- **Moteur** 480 kW, force max 9 kN ; freins 24 kN, répartition réglable ; appui ClA 3,4 (balance 41 %
  avant) ; traînée CdA 1,3 → ~300 km/h en pointe.
- **Thermique pneus** par essieu : chauffe = 0,35 × puissance de glissement + échauffement de carcasse ;
  refroidissement ∝ vitesse ; arrière plus volumineux (refroidit ×1,3, capacité ×1,15). Facteur
  d'adhérence `1 − 0,25 z²` (plancher 0,7) avec z = écart à la fenêtre optimale. Surchauffe → usure ×2.
- **Usure** ∝ énergie de glissement ; perte d'adhérence `0,28 × usure^forme` ; 100 % = crevaison.
  Calibrage mesuré (pilote « pro ») : usure/tour tendre ~0,11, medium ~0,063, dur ~0,038.
- **Aéro** : aspiration jusqu'à 40 m (−32 % de traînée), air perturbé jusqu'à 30 m (−22 % d'appui),
  dans un sillage de ±2,6 m.
- **Météo** (`weather.py`) : script de pluie déterministe par manche (30 % de manches pluvieuses),
  humidité par cellule de 10 m qui monte/sèche, zones à séchage lent. Slicks −55 % d'adhérence sur piste
  détrempée ; pneus pluie −10 % mais surchauffent sur le sec.
- **Collisions** : voitures = 3 cercles ; impulsion avec restitution 0,25 et effet de lacet ; dégâts
  = `(vitesse de rapprochement − 1,5)² / 250` (≥ 17 m/s ≈ voiture détruite). Les dégâts réduisent
  l'appui et augmentent la traînée.
- **État packé** : `World.S` (n × 32) et `World.PRM` (n × 40), colonnes nommées dans `kernels.SC/PC`,
  exposées en vues (`world.vx`…). Événements (tours, murs, pneus) remontés par un tableau de drapeaux
  et traités en Python.
- **Performances mesurées** (2 cœurs) : ~3 700 décisions/s pour 1 voiture pilotée par le pilote de
  référence (le pilote Python est le goulot) ; course 16 voitures × 14 tours ≈ 90 s CPU ; un World de
  64 voitures fantômes ≈ 1–2 ms par décision.
- Décision technique : **pas de Rust**. Le cœur numba est assez rapide, portable (Windows de Gabriel) et
  isolé : un portage Rust reste possible si l'entraînement est limité par la simulation (à mesurer).

### 4.3 Observations et actions — `raicing/sensors.py`, `raicing/world.py`
Observation v1, **157 valeurs** (`OBS_LAYOUT`) : sa voiture (20 : vitesses, dérive, lacet, braquage,
pédale, position latérale, cap relatif, températures et usure avant/arrière, dégâts, gomme one-hot,
fantôme), piste devant (16 points de 5 à 270 m × [x, y en repère voiture, demi-largeur, courbure]),
météo (6), course (10 : type de session, tours restants, position, écarts, distance à l'entrée des
stands…), 6 adversaires les plus proches (8 valeurs chacun), coéquipier (9, dont le message radio reçu).
Actions, **11 valeurs** : `[braquage, pédale −1…+1, demande d'arrêt, 4 scores de gomme, 4 valeurs radio]`.

### 4.4 Pilotes de référence — `raicing/drivers/`
Pilotes **scriptés** (non autorisés pour les écuries engagées). Ligne de course par « bande élastique »,
profils de vitesse pré-calculés pour une grille de niveaux d'adhérence, direction feed-forward + Stanley,
contrôle de traction et freinage au seuil, gestion du trafic (couloir latéral, suivi, dépassement),
stratégie d'arrêts (usure, météo). Personnalités : `debutant`, `rapide`, `prudent`, `pneus`,
`cooperatif`, `pro`. Fiabilité mesurée : `pro` 0 sortie / 30 circuits ; course 16 voitures ≈ 13 classés.
Ils servent d'écuries système, de professeurs pour l'imitation, d'adversaires d'entraînement et d'étalon
du Driver Rating.

### 4.5 Cerveaux — `raicing/brains/spec.py`
Format `raicing-brain/1` : `brain.json` (couches `linear`, `relu`, `tanh`, `gelu`, `sigmoid`, `elu`,
`silu`, `layernorm`, `gru`, `residual`, normalisation d'entrée `obs_norm`, sortie 2–11 valeurs avec
`tanh`/`clip`/`none`) + `weights.npz`. Exécution numpy, déterministe, **aucun code arbitraire** (sûr à
exécuter sur le serveur). Plafonds : 2 M paramètres, 48 couches. Raison du choix : fidélité à l'idée de
« cerveaux », sécurité, impossibilité de tricher avec un pilote scripté.

### 4.6 Course — `raicing/race.py`
`run_qualifying`, `run_race`, `classify` (positions, temps + pénalités, écarts, points, meilleur tour,
incidents nommés), replays `raicing-replay/1` (images à 5 Hz, ~10 Mo JSON par course, gzip ensuite).
Format détaillé dans `docs/API.md`.

## 5. Couche « ligue »

### 5.1 Économie — `raicing/league/economy.py` (fini, smoke test)
Budget identique (1 000 en S1, puis 850–1 150 tiré par saison), pas de revenus, report ≤ 20 %.
7 pièces niveaux 0–10 (moteur, appui, carrosserie basse traînée, châssis, freins, refroidissement,
suspension), gain `max × (1 − e^(−L/3,5))` normalisé (rendements décroissants), coût ×1,35 par niveau.
Capacités : encombrement (12 + 2/niveau de châssis), charge thermique (8 + 2/niveau de refroidissement),
masse. Compatibilités (appui ≥ 7 exige châssis ≥ 4, etc.). 6 technologies. Après un changement de
règlement, la pièce concernée est plafonnée à 60 % de son niveau tant qu'elle n'est pas révisée.
`modifiers(garage)` → `PerformanceModifiers` → `build_spec`.

### 5.2 Règlement — `raicing/league/regulations.py` (fini, non testé en conditions réelles)
Catalogue de 15 paramètres modifiables ; un changement par intersaison (5–10 %, parfois jusqu'à 15 %) ;
**banc historique** : anciens champions + pilotes de référence courent sous l'ancien et le nouveau
règlement ; rejet si perte de temps au tour > 5 % ou nouvelles sorties.

### 5.3 Driver Rating — `raicing/league/rating.py` (fini, testé sur les pilotes de référence)
Six composantes sur circuits de validation, 50 = niveau du « pro » : vitesse (3 circuits), régularité,
survie, pluie, gestion des pneus (relais de 8 tours en tendres), trafic (départ en dernière position
derrière 5 pilotes de référence). Note globale pondérée + intervalle d'incertitude. Purement informatif.

### 5.4 Laboratoire d'entraînement — `raicing/lab/` (écrit par un sous-agent, partiellement vérifié)
Spécification : `docs/LAB.md`. Point d'entrée `raicing.lab.jobs.run_job(config, regs, init_brain,
quota_remaining, workdir, progress, should_stop)`. Méthodes : PPO/SAC/TD3 (stable-baselines3 sur un
`VecEnv` vectorisé maison), stratégies d'évolution, imitation (BC/DAgger) d'un pilote de référence.
Modes : `solo`, `race`, `traffic`. Quota compté à la source.

**Résultat mesuré important** (`demo_brains/demo-bc`, `demo-report.json`) : un pilote **entraîné par
imitation** du « pro » (1,6 M interactions, ~14 min) boucle 2 tours sans sortie sur **5/5 circuits de
labo** et **4/5 circuits de validation** (sortie au 2ᵉ tour sur `validation_track(2)`), à **0–2 %** du
temps du « pro » (sauf val:2). Le critère d'acceptation (≥ 3/5 et ≥ 3/5) est donc atteint pour BC.
Démontré depuis : l'affinage PPO de ce pilote (`demo_brains/demo-ppo`, 4 M interactions, 6 min) boucle
20/20 circuits de validation et bat le « pro » sur 9/20 (voir `docs/LAB.md`, « Résultats mesurés »).

### 5.5 Services, API, worker, CLI (écrits par un sous-agent, testés par sa suite)
Spécification : `docs/LEAGUE.md` et `docs/API.md`.
- `league/models.py` (SQLAlchemy 2, SQLite/PostgreSQL), `league/db.py`, `league/service.py`
  (`LeagueService` : cycle de vie complet), `league/training.py` (adaptateur vers `run_job`),
  `league/live.py` (diffusion du direct).
- `api/app.py` (FastAPI : lecture publique, espace écurie par jeton Bearer, administration), `api/worker.py`
  (jobs d'entraînement + manches programmées).
- `cli/paddock.py` (directeurs), `cli/stewards.py` (administration), modes `local` et `remote`, `--json`.

### 5.6 Site web — `web/` (écrit par un sous-agent)
Next.js App Router, TypeScript, CSS maison, français. Pages : accueil, saison, course (visionneuse de
replay en canvas : interpolation, caméra, classement en direct, télémétrie, événements), direct
(WebSocket), écuries, écurie, pilote (Driver Rating), règlement, à propos. Mode **mock** (`RAICING_MOCK=1`)
construit une ligue plausible à partir de `web/fixtures/`. Captures dans `web/screenshots/` (le site a
donc été construit au moins une fois). `web/Dockerfile` (standalone).

### 5.7 Replay « retransmission TV » (`web/components/replay/broadcast.ts`)
Demande de Gabriel : du **spectacle**. Le replay est rendu comme une retransmission : circuit urbain de nuit
(murs éclairés, vibreurs dans les virages, damier, lampadaires, blocs de ville), monoplaces aux couleurs
des écuries avec traînées et feux stop (champ `pedal`), étincelles aux contacts, fumée aux abandons, pluie
à l'écran, piste mouillée et gerbes d'eau. **Caméra « réalisateur »** par défaut : elle suit la bagarre la
plus serrée en tête, coupe sur les incidents (contact, abandon, dépassement, nouveau leader), plans limités
en temps réel et en temps de course. Habillage HTML : classement en colonne, compteur de tours, bandeaux
d'événements, fiche du pilote filmé, **feux de départ** et bouton « Regarder la course ». Les dépassements
et meilleurs tours sont déduits des images (`buildHighlights`). 60 images/s mesurées en Chromium sans GPU.

Le reste du site suit la même ambiance : thème sombre unique (plus de thème clair), titres en Barlow
Condensed italique (auto-hébergée, `web/app/fonts/`, licence OFL), cartes à liseré vert/rouge, podiums
et positions marqués, et sur l'accueil un grand bandeau où la dernière course tourne en fond
(`web/components/HeroReplay.tsx`, image fixe si l'utilisateur réduit les animations).

## 6. État des tests (mis à jour le 14/10/2026)

`python -m pytest -q` : **65 réussis, 0 échec** (≈ 35 s). Les 3 échecs précédents de `tests/test_lab.py` :
- `test_crash_penalty_and_reset` et `test_vecenv_shapes[traffic]` : erreurs des tests (les deux voitures
  ne touchent jamais le mur à la même décision ; un pilote aléatoire à mi-pédale part trop lentement en
  `traffic` pour sortir en 6 s). Tests corrigés, code inchangé.
- `test_es_one_generation` : vrai défaut de `algos/es.py`, qui lançait une génération tronquée avec le
  reliquat du quota (membres comparés sur des épisodes inégaux). Une génération ne démarre plus que si
  elle tient entière dans le quota (sauf la première).

Installation en environnement cloud : `download.pytorch.org` est bloqué par le proxy, `pip install`
récupère donc torch depuis PyPI (version CUDA, lourde mais fonctionnelle).

Corrigé : en mode `race`, les départs lancés (75 %) partaient avec `progress = s` et pouvaient « finir »
en quelques décisions (bonus d'arrivée gratuit). Ils partent désormais avec `progress = s − L` : la voiture
termine son tour puis court `laps` tours complets, comme depuis la grille.

Non vérifié par la session principale : relecture de `service.py` (83 Ko) contre la spec.

## 7. Prochaines étapes, dans l'ordre

1. ~~Faire passer les 3 tests du labo~~ (fait le 14/10/2026).
2. ~~Prouver que l'apprentissage par renforcement améliore un pilote~~ (fait le 14/10/2026 : `demo-ppo`,
   20/20 circuits de validation, plus rapide que le « pro » sur 9/20 ; détails et débits dans `docs/LAB.md`).
3. ~~Écrire `docs/DIRECTOR_GUIDE.md`~~ (fait le 14/10/2026, exemples `--json` relevés sur une ligue locale réelle).
4. ~~Vérifier le site~~ (fait le 14/10/2026) : build et typage OK ; 41 pages en mode maquette et 38 pages
   branchées sur l'API réelle (ligue locale avec une manche courue) répondent sans erreur, sans erreur
   JavaScript ni débordement horizontal (1440 px et 390 px) ; lecture du replay et caméra « Suivre »
   vérifiées. Corrigés : nombre d'écuries écrit en dur sur l'accueil, écart incohérent affiché pour une
   voiture aux stands dans le replay. Captures à jour dans `web/screenshots/`.
5. **Équilibrage** : simuler des stratégies 0/1/2 arrêts sur plusieurs circuits (il faut que plusieurs
   soient viables selon le circuit), coût/gain des pièces, taux d'abandons au premier tour.
6. **Saison de démonstration** : ligue locale (`stewards init`), écuries système + 1–2 écuries avec des
   cerveaux entraînés, 10 manches, puis publication d'un aperçu.
7. **Déploiement** : `docker-compose` (postgres, api, worker, web), CI GitHub Actions (pytest + build web).
   Le site peut aller sur Vercel (connecté) ; l'API et le worker (Python, CPU, long) ont besoin d'un
   serveur à part (VPS, Railway, Fly…) — c'est une décision de coût à soumettre à Gabriel.

## 8. Décisions et leurs raisons (à ne pas remettre en cause sans en parler à Gabriel)

| Décision | Raison |
|---|---|
| Règle de responsabilité + pénalité d'écurie | Empêche le sacrifice d'un coéquipier ; choisie par Gabriel |
| Rebord = élimination | Règle voulue par Gabriel ; risque d'hécatombes au 1er tour à surveiller |
| Pilotes = cerveaux déclaratifs | Sécurité serveur, déterminisme, esprit du projet |
| Quota sur l'honneur en mode local | Accepté par Gabriel ; contrôlé seulement en mode serveur |
| Python + numba plutôt que Rust | Suffisant, portable, plus simple à faire évoluer ; Rust si mesure contraire |
| Site Next.js complet hébergé | Choix de Gabriel (plutôt qu'une simple page de visionnage) |
| CLI `paddock` pour les directeurs | Choix de Gabriel ; marche avec toute IA qui a un terminal |
| Pas de pilote scripté pour les écuries | Sinon un planificateur codé à la main battrait l'apprentissage |

Point d'éthique signalé à Gabriel : la session qui construit le moteur (Opus) est aussi candidate pour
diriger une écurie contre Astra. Parade retenue : moteur et règlement **publics** pour toutes les IA
directrices, graines de championnat secrètes, courses exécutées par les commissaires.

## 9. Pièges connus

- Premier appel au moteur = compilation numba (quelques secondes, mise en cache ensuite).
- `World.idx` renvoie une **copie** ; les vues (`world.vx`…) pointent dans `world.S` : affecter un
  élément est OK, ne jamais réaffecter l'attribut hors des setters fournis.
- En session `lab`, ni l'arrêt prolongé ni le demi-tour n'éliminent : tronquer les épisodes soi-même.
- Dans un même `World` en mode `race`, le premier arrivé déclenche le drapeau pour tous : pour des
  courses indépendantes en entraînement, utiliser `lab` + comptage de tours maison.
- Les pilotes de référence sont en Python pur : ils dominent le temps de calcul d'une course.
- Environnement cloud : 2 cœurs, pas de GPU ; Chromium dans `/opt/pw-browsers` pour Playwright.
- Dépôt : `https://github.com/Joks6669/rAIcing`, branche **`main`**. Terminer chaque commit par les lignes
  d'attribution demandées par l'environnement.
