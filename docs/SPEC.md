# AI Racing League — Spécification V1.2

Cette version transforme le game design V1.1 en règles exécutables. Les valeurs numériques
exactes vivent dans `raicing/config.py` (classe `Regulations`) : ce document en donne le sens,
le code fait foi.

Principe central : **les IA dirigent, construisent, apprennent, communiquent et pilotent ; le
système définit les règles et les applique ; l'humain observe.**

---

## 1. Règles sportives

| Élément | Règle |
|---|---|
| Saison | 10 manches sur circuits procéduraux inédits, révélés après la course |
| Écurie | 2 voitures, 2 pilotes distincts (deux cerveaux avec leurs propres paramètres) |
| Direction | Une IA directrice par écurie ; aucune intervention entre le départ et l'arrivée |
| Qualifications | Un tour de lancement puis un seul tour chronométré, chaque voiture seule en piste, conditions identiques |
| Course | 10 à 15 tours selon la longueur du circuit (durée cible homogène) |
| Grille | Ordre des qualifications ; voitures sans temps en fond de grille, dans l'ordre du championnat |
| Départ | Arrêté, grille en quinconce, feux éteints à t = 0 |
| Carburant / safety car | Aucun |
| Pneus | Tendre, medium, dur, pluie ; changement libre à chaque arrêt |
| Arrêts | Aucun obligatoire, illimités, temps perdu fixé par le règlement |
| Abandon | Contact avec un rebord, voiture cassée (dégâts ≥ 100 %), pneu détruit (usure ≥ 100 %), arrêt prolongé, demi-tour |
| Barème | 25-18-15-12-10-8-6-4-2-1 pour les dix premiers ; écurie = somme de ses deux voitures |
| Fin de course | Quand le leader boucle le dernier tour, les autres terminent leur tour en cours ; délai maximal ensuite |

### 1.1 Contacts entre voitures (règle de responsabilité)

Les collisions sont physiques. À chaque contact, les commissaires automatiques désignent un fautif :

1. **Contact par l'arrière** (écart longitudinal > 2,5 m) : la voiture qui arrive par l'arrière est
   fautive. Exception : **freinage injustifié** — si la voiture de devant a freiné fort dans la
   seconde précédente alors que sa vitesse était nettement inférieure à la vitesse de passage
   possible sur les 150 m suivants, c'est elle qui est fautive.
2. **Contact côte à côte** : la voiture qui se déplaçait latéralement vers l'autre (vitesse
   latérale relative au circuit) est fautive. Si les deux vitesses latérales sont proches, c'est
   un incident de course sans fautif.

Sanctions :

- Contact fautif sans conséquence grave : **+5 s** au temps de course.
- Si la victime est éliminée dans les 3 s qui suivent le contact : le fautif est **disqualifié**
  sur-le-champ et son **écurie perd 15 points** au championnat. Le sacrifice d'un coéquipier pour
  éliminer un rival ne paie donc jamais (25 points pris au rival, mais 15 perdus et une voiture
  disqualifiée).
- Les sanctions s'appliquent aussi entre coéquipiers.

### 1.2 Arrêts aux stands

Le pilote active une demande d'arrêt et choisit la gomme. Il entre aux stands en franchissant
l'entrée (50 m avant la ligne). Il est alors retiré de la piste pendant
`pit_lane_time + service_time` (+ réparation si dégâts > 15 %), puis ressort 150 m après la ligne
à 80 km/h, côté droit, sans collision pendant 2 s. Les pneus neufs sortent à 70 °C.

---

## 2. Physique (2D, déterministe)

- Pas physique fixe de 1/100 s ; **décisions des pilotes à 20 Hz** (toutes les 5 étapes). Le
  rendu n'influence jamais la simulation.
- Modèle bicyclette dynamique : masse, inertie, centre de gravité, empattement.
- **Pneus** : formule « magique » simplifiée (adhérence maximale vers 7° de dérive puis
  décrochage), ellipse de friction (accélérer ou freiner réduit l'adhérence latérale),
  sensibilité à la charge, **transferts de masse** longitudinaux et latéraux.
- **Température** par essieu : chauffe par l'énergie de glissement, refroidissement par l'air.
  Chaque gomme a une fenêtre optimale ; hors fenêtre, l'adhérence chute ; en surchauffe,
  l'usure double.
- **Usure** proportionnelle à l'énergie de glissement ; perte d'adhérence progressive ; à 100 %,
  crevaison et abandon.
- **Aérodynamique** : appui et traînée ∝ v². Derrière une voiture : **aspiration** (moins de
  traînée jusqu'à 40 m) et **air perturbé** (moins d'appui jusqu'à 30 m). Les coéquipiers
  peuvent l'exploiter.
- **Météo** : intensité de pluie évolutive ; humidité par segment de 10 m, certaines zones
  sèchent plus lentement. Les slicks perdent jusqu'à 55 % d'adhérence sur piste mouillée ; les
  pneus pluie surchauffent sur le sec.
- **Dégâts** : les chocs accumulent des dégâts qui réduisent l'appui et augmentent la traînée.

Aucune difficulté n'est aléatoire pendant la course : la seule source de variation est la graine
de la manche (circuit, météo), identique pour tous.

---

## 3. Les pilotes : format « cerveau »

Un pilote est un **réseau de neurones déclaratif** : un fichier `brain.json` (architecture) et un
fichier `weights.npz` (poids). Aucun code arbitraire n'est exécuté. Le moteur exécute le réseau
en numpy, de façon déterministe.

Couches disponibles : `linear`, `relu`, `tanh`, `gelu`, `sigmoid`, `layernorm`, `gru` (mémoire
d'un pas à l'autre), `residual` (bloc contenant d'autres couches). Normalisation d'entrée
optionnelle. Plafonds d'exécution : 2 millions de paramètres, 48 couches.

### 3.1 Observations (version 1, 130 valeurs)

Le pilote reçoit à chaque décision, en repère voiture et normalisé :

- **Sa voiture** : vitesses, dérive, lacet, braquage, position latérale, cap relatif à la piste,
  température et usure des pneus avant/arrière, gomme, dégâts.
- **La piste devant lui** : 16 points de la ligne médiane de 5 à 270 m avec la demi-largeur
  locale ; humidité à 0, 50, 100 et 200 m ; intensité de pluie ; température de piste.
- **La course** : tours restants, position, écarts devant/derrière, distance à l'entrée des
  stands, statut stands, type de session.
- **Les adversaires** : les 6 voitures les plus proches (dans les 150 m) — position relative,
  vitesse relative, cap, coéquipier ou non.
- **Son coéquipier** : écart, vitesse, usure, et **le message radio** reçu (4 valeurs).

### 3.2 Actions (11 valeurs)

`[braquage, pédale (−1 frein … +1 gaz), demande d'arrêt, 4 scores de gomme, 4 valeurs de message]`.
Un cerveau peut ne produire que les 2 premières : les autres valent alors 0.

Le canal radio est numérique et libre : les écuries inventent leur propre protocole.

---

## 4. Écuries, économie et réglementation

- **Budget saisonnier identique pour tous** : 1 000 points en saison 1, puis tiré entre 850 et
  1 150 avant chaque saison. Aucun revenu. Report d'au plus 20 % du budget non dépensé.
- **Construction** : 7 pièces (moteur, appui, carrosserie basse traînée, châssis, freins,
  refroidissement, suspension), niveaux 0 à 10, **rendements décroissants**, coût ×1,35 par niveau.
- **Capacité technique** : encombrement, charge thermique et masse limités par le châssis et le
  refroidissement ; compatibilités entre pièces. On ne peut pas tout empiler.
- **Recherche** : technologies à débloquer (refroidissement actif, composites, aileron basse
  traînée, etc.).
- **Révision** : après un changement de règlement, les pièces concernées sont plafonnées tant
  qu'elles ne sont pas révisées.
- **Réglages d'avant-course** : niveau d'aileron, répartition de freinage, gomme de départ.
- **Réglementation** : un changement après chaque saison, sur un seul paramètre (5 à 10 %,
  15 % au maximum). Le changement est validé par un **banc historique** (anciens champions et
  pilotes de référence) et rejeté s'il détruit trop brutalement leurs performances.

---

## 5. Entraînement (laboratoire)

- **Quota identique** d'interactions par saison (une interaction = une décision d'un pilote dans
  un simulateur d'entraînement), indépendant de l'algorithme.
- Circuits du laboratoire ≠ circuits de validation ≠ circuits de championnat (graines séparées).
- Méthodes intégrées : **PPO, SAC, TD3** (stable-baselines3), **stratégies d'évolution**
  (neuroévolution), **imitation** à partir des pilotes de référence. Architecture, récompense,
  curriculum et adversaires d'entraînement sont choisis par l'écurie.
- Les générations sont conservées et réutilisables d'une saison à l'autre.
- En ligue officielle, l'entraînement passe par le serveur, qui compte le quota. En mode local,
  le quota est déclaratif et chaque génération embarque son journal d'entraînement.

---

## 6. Driver Rating

Outil d'évaluation uniquement (aucun effet en course). Batterie de tests sur des circuits de
validation fixes : **vitesse pure, régularité, survie, pluie, gestion des pneus, trafic**.
Chaque note est normalisée par rapport aux pilotes de référence ; la note globale est fournie
avec un intervalle d'incertitude.

---

## 7. Contrats de données

- **Replay** (`replay.json.gz`) : métadonnées (circuit, voitures, écuries), géométrie du
  circuit, puis images à 10 Hz : pour chaque voiture `x, y, cap, vitesse, statut, gomme, usure,
  température, tour, position`, plus les événements (dépassements, arrêts, contacts, sanctions,
  abandons) et la météo.
- **Résultats** : classement, temps, tours, meilleur tour, arrêts, statut (`finished`, `dnf`,
  `dsq`), sanctions, points.
- Les schémas exacts sont dans `raicing/race.py` (`RaceResult`) et `raicing/replay.py`.
