# Équilibrage — mesures et décisions (14/10/2026)

Trois bancs d'essai reproductibles, tous avec les pilotes de référence (scriptés) sur les circuits de
validation, temps sec. Les résultats bruts sont dans `balance/`.

| Banc | Commande | Question |
|---|---|---|
| Stratégies | `python scripts/balance_strategy.py --tracks 6` | 0, 1 et 2 arrêts sont-ils tous viables ? |
| Garage | `python scripts/balance_garage.py --tracks 4` | Que rapporte chaque pièce, par point dépensé ? |
| Course | `python scripts/balance_race.py --tracks 6` | Abandons, abandons au 1er tour, poids de la grille |

`balance_strategy.py` accepte `--set chemin=valeur` pour tester une variante du règlement
(ex. `--set race.pit_lane_time=12`).

## 1. Stratégies d'arrêts : équilibrées, rien à changer

Le « pro » court seul une course complète avec un plan imposé (58 plans par circuit, 348 courses).
Meilleur plan de chaque famille :

| Circuit | Tours | 0 arrêt | 1 arrêt | 2 arrêts | Gagnant | Écart 2ᵉ famille |
|---|---|---|---|---|---|---|
| val:0 | 15 | 1255,8 s (dur) | **1249,6 s** (M→M) | 1257,3 s | 1 arrêt | 6,2 s |
| val:1 | 14 | **1205,1 s** (dur) | 1214,0 s | 1229,1 s | 0 arrêt | 8,9 s |
| val:2 | 15 | 1174,7 s | 1135,2 s | **1134,4 s** (T→T→T) | 2 arrêts | 0,8 s |
| val:3 | 15 | **1022,1 s** (dur) | 1032,2 s | 1046,8 s | 0 arrêt | 10,1 s |
| val:4 | 12 | 1288,4 s | 1259,4 s | **1251,2 s** (T→T→T) | 2 arrêts | 8,1 s |
| val:5 | 15 | **880,4 s** (dur) | 893,7 s | 910,4 s | 0 arrêt | 13,3 s |

Les trois familles gagnent selon le circuit, avec des écarts de 1 à 13 s : la stratégie compte et
dépend du circuit. Un tendre sans arrêt crève toujours avant l'arrivée. En course, la position en piste
favorise un peu plus les stratégies à peu d'arrêts.

Les nombreux arrêts sous la pluie (4–5 dans la manche de test) sont logiques : la pluie y a commencé
et cessé deux fois, chaque bascule pneus pluie / slicks coûte un arrêt.

## 2. Garage

Gain de temps au tour (2 tours, « pro » seul) selon le niveau d'une seule pièce :

| Pièce | Niv. 1 (coût) | Niv. 3 (coût) | Niv. 5 (coût) | Gain pour 100 pts (niv. 1 → 5) |
|---|---|---|---|---|
| Moteur | 0,74 % (70) | 1,56 % (292) | 1,90 % (697) | 1,06 → 0,27 % |
| Appui | 0,62 % (65) | 1,27 % (271) | 1,60 % (647) | 0,95 → 0,25 % |
| Suspension | 0,48 % (50) | 1,05 % (209) | 1,36 % (498) | 0,97 → 0,27 % |
| Carrosserie | 0,42 % (55) | 0,97 % (229) | 1,27 % (547) | 0,77 → 0,23 % |
| Châssis | 0,12 % (60) | 0,29 % (250) | 0,37 % (597) | 0,20 → 0,06 % |
| Refroidissement (avant) | −0,13 % | −0,37 % | −0,54 % | **négatif** |
| Refroidissement (après) | +0,05 % | +0,05 % | +0,04 % | ≈ 0 (pièce de capacité) |
| Freins | −0,01 % | −0,06 % | −0,10 % | **nul** |

Technologies : récupération d'énergie +0,50 % (150 pts), aileron basse traînée +0,28 % (100 pts),
télémétrie pneumatique ≈ 0 sur un tour (son effet, −5 % d'usure, joue sur un relais).

- **Sain** : moteur, appui, suspension et carrosserie sont au coude à coude, avec des rendements
  décroissants nets. Aucune pièce dominante, l'étalement du budget est la bonne stratégie.
- **Châssis** : faible en vitesse pure, mais c'est une pièce de capacité (encombrement, appui ≥ 7).
- **Refroidissement, corrigé** : la pièce multipliait le refroidissement des pneus, qui sortaient alors
  de leur fenêtre de température en conditions normales (−0,66 % au niveau 7). Elle élargit désormais
  la fenêtre de température optimale de chaque gomme (+14 % au maximum) : utile si les pneus sont trop
  chauds ou trop froids, jamais pénalisante. Sa valeur principale reste la capacité thermique (moteur).
- **Freins, à décider** : au freinage maximal, les roues bloquent et l'adhérence du pneu limite la
  décélération, quelle que soit la force des freins. Distance 288 → 108 km/h : 94,4 m au niveau 0,
  93,2 m au niveau 7. La pièce ne sert pratiquement à rien. Voir « Questions ouvertes ».

## 3. Courses à 16 voitures

16 pilotes de référence (personnalités mélangées, voitures identiques), 6 circuits :

| | Avant | Après correctifs |
|---|---|---|
| Voitures à l'arrivée | 64 / 96 | 67 / 96 |
| Sorties (mur) | 23 | 15 |
| Voitures cassées | 3 | 6 |
| Disqualifications (règle de responsabilité) | 6 | 8 |
| Abandons au 1er tour | 0 | 1 |

Corrélation grille → arrivée de −0,06 à 0,99 selon le circuit : la qualification compte sans tout figer.

Cause principale des sorties, corrigée : en approche d'un virage rapide (≈ 250 m de rayon, 295 km/h),
un pilote tentait de dépasser la voiture 28 m devant en visant la limite côté mur ; à cette vitesse il
débordait de 1,7 m au-delà de sa cible et touchait le mur. Le même scénario se répétait à chaque tour
(9 abandons sur val:5). Désormais un dépassement garde une marge au mur qui croît avec la vitesse, et
n'est pas tenté s'il ne reste pas la place. La vitesse visée tient aussi compte d'une trajectoire plus
serrée quand le trafic écarte la voiture de la trajectoire idéale. **Les temps du « pro » seul sont
inchangés au millième** (étalon du Driver Rating).

Les abandons restants viennent surtout des accrochages (avec disqualification du fautif), de la
personnalité « rapide » (« Rapide mais risqué ») et des dépassements de retardataires en fin de course.
L'art du dépassement des pilotes de référence reste perfectible.

## 4. Écuries système

Les deux pilotes d'une écurie système avaient la même personnalité, la même voiture et le même réglage :
le moteur étant déterministe, ils signaient exactement le même temps en qualifications. Le second pilote
roule désormais avec un réglage légèrement différent (aileron 0,42, répartition de freinage 0,56) :
écarts de 0,05 à 0,2 s entre coéquipiers.

## 5. Questions ouvertes (décision de Gabriel)

1. **Freins.** Trois options :
   - (a) *recommandée* : un effet « anti-blocage » — la pièce garde plus de direction au freinage et
     rapproche la décélération du maximum du pneu. Utile en entrée de virage, ne change pas la voiture de base ;
   - (b) baisser la force de freinage de base pour que les freins limitent à haute vitesse (change la
     voiture de base et tout l'étalonnage, à refaire) ;
   - (c) remplacer la pièce par autre chose.
2. **Attrition** : ~30 % d'abandons (disqualifications comprises) avec les pilotes de référence. Acceptable
   pour le spectacle ? Sinon, chantier dédié sur le dépassement des pilotes de référence.
