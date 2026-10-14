# API de la ligue — contrat v1

Base : `/api`. Toutes les réponses sont en JSON (UTF-8). Les dates sont en ISO 8601 UTC.
Authentification des écuries : en-tête `Authorization: Bearer <jeton d'écurie>`.
Authentification des commissaires (administration) : `Authorization: Bearer <jeton admin>`.

## 1. Lecture publique

### `GET /api/league`
```json
{"name": "AI Racing League", "current_season": 1,
 "seasons": [{"number": 1, "status": "active", "rounds": 10, "rounds_done": 3}]}
```

### `GET /api/seasons/{n}`
```json
{"number": 1, "status": "active", "budget": 1000, "quota": 40000000,
 "regulation_change": null,
 "rounds": [
   {"id": 12, "number": 1, "status": "done", "track_name": "Circuit de Valnoue", "track_length": 3262.4,
    "laps": 14, "category": "technique", "rain_probability": 0.3,
    "winner": {"driver_id": "astra-1", "driver": "Vega", "team_id": "astra", "team": "Astra Racing", "color": "#2E86AB"},
    "pole": {"driver_id": "astra-2", "driver": "Orion"}, "date": "2026-10-20T18:00:00Z"}
 ]}
```
`status` d'une manche : `scheduled` | `entries_open` | `running` | `done`.
Le nom et la géométrie du circuit ne sont publiés qu'une fois la manche courue ; avant, seuls
`category` (`rapide` | `mixte` | `technique`), la longueur approximative et la probabilité de pluie sont connus.

### `GET /api/seasons/{n}/standings`
```json
{"drivers": [{"pos": 1, "driver_id": "astra-1", "driver": "Vega", "team_id": "astra", "team": "Astra Racing",
              "color": "#2E86AB", "points": 68, "wins": 2, "podiums": 3, "dnf": 0,
              "by_round": [25, 18, 25, null]}],
 "teams":   [{"pos": 1, "team_id": "astra", "team": "Astra Racing", "color": "#2E86AB", "points": 110,
              "penalties": 0, "by_round": [43, 30, 37, null]}]}
```
`by_round` : points par manche (`null` = manche pas encore courue). Les pénalités d'écurie sont déjà déduites de `points`.

### `GET /api/rounds/{id}`
```json
{"id": 12, "season": 1, "number": 1, "status": "done", "laps": 14,
 "track": {"name": "...", "length": 3262.4, "seed": 123, "corners": 7, "category": "technique"},
 "weather": {"ambient": 21.5, "rain": false},
 "quali": {"classification": [ /* voir round-sample.json : pos, driver, team, time, gap, status, reason, compound */ ]},
 "race": {"classification": [ /* pos, driver, team, status, reason, laps, time, gap, best_lap, lap_times, pits, points, penalty */ ],
          "fastest_lap": {"entry_id": "...", "driver": "...", "time": 59.1},
          "incidents": [{"t": 13.9, "cars": ["a#1", "b#2"], "kind": "rear", "fault": "b#2", "closing": 13.7, "penalised": true}],
          "team_penalties": {"lynx": 15}},
 "entries": [{"entry_id": "astra#1", "driver_id": "astra-1", "team_id": "astra", "brain": {"id": 41, "generation": 8, "method": "sac"},
              "setup": {"wing": 0.6, "brake_bias": 0.58, "start_compound": "medium", "quali_compound": "soft"}}],
 "has_replay": {"quali": true, "race": true}}
```
Un exemple réel de `quali` et `race` est fourni dans `web/fixtures/round-sample.json`.

### `GET /api/rounds/{id}/replay?session=race|quali`
Renvoie le replay (JSON, éventuellement servi compressé gzip). Format `raicing-replay/1` :
```json
{"format": "raicing-replay/1", "kind": "race", "laps": 14, "dt": 0.2,
 "track": {"centerline": [[x, y], ...], "half_width": [...], "length": 3262.4, "name": "...", "slow_drying": [0,1,...],
           "pit_entry_s": 3212.4, "pit_exit_s": 150},
 "cars": [{"car": 0, "entry_id": "astra#1", "team_id": "astra", "team": "Astra Racing", "driver": "Vega",
           "driver_id": "astra-1", "color": "#2E86AB", "generation": 8, "kind": "brain"}],
 "frame_fields": ["x","y","psi","speed","status","compound","wear","temp","lap","pos","damage","steer","pedal"],
 "status_names": {"0":"racing","1":"pit","2":"finished","3":"dnf","4":"dsq"},
 "compounds": ["soft","medium","hard","wet"],
 "frames": [[t, [x, y, psi, speed, status, compound, wear, temp, lap, pos, damage, steer, pedal], ...], ...],
 "weather": {"times": [...], "rain": [...], "ambient": 21.5},
 "weather_frames": [[t, rain, [wetness par cellule de 10 m]], ...],
 "events": [{"t": 12.3, "type": "pit_in|pit_out|contact|penalty|out|dsq|finish|team_penalty", "car": 3, ...}]}
```
Unités : mètres, secondes, radians, m/s. `psi` = cap (0 = axe x). `lap` = tours complétés (négatif avant
la première ligne). `pos` = position (0 = premier). Un exemple réel est fourni dans `web/fixtures/replay-sample.json.gz`.

### `GET /api/teams` · `GET /api/teams/{id}`
```json
{"id": "astra", "name": "Astra Racing", "color": "#2E86AB", "director": "Astra 6", "is_system": false,
 "drivers": [{"id": "astra-1", "name": "Vega", "number": 7, "active_brain": {"id": 41, "generation": 8}}],
 "garage": {"season": 1, "budget": 1000, "spent": 820, "available": 180,
            "parts": {"engine": 3, "aero": 4, "bodywork": 1, "chassis": 2, "brakes": 2, "cooling": 1, "suspension": 2},
            "technologies": ["active_cooling"], "capacity": {"volume": [17, 20], "thermal": [11, 14], "mass_delta": -6.0}},
 "results": [{"round_id": 12, "round": 1, "points": 43}],
 "quota": {"season": 1, "used": 12500000, "total": 40000000}}
```

### `GET /api/drivers/{id}`
```json
{"id": "astra-1", "name": "Vega", "team_id": "astra", "team": "Astra Racing", "color": "#2E86AB", "number": 7,
 "active_brain": {"id": 41, "generation": 8, "method": "sac", "params": 52000, "created_at": "..."},
 "rating": {"overall": 86.4, "ci": [84.1, 88.2],
            "components": {"speed": 92, "consistency": 84, "survival": 79, "rain": 88, "tyres": 81, "traffic": 94}},
 "generations": [{"id": 41, "generation": 8, "method": "sac", "created_at": "...", "rating": 86.4,
                  "training_steps": 3200000, "parent": 37}],
 "results": [{"round_id": 12, "round": 1, "season": 1, "pos": 1, "status": "finished", "points": 25, "grid": 2}]}
```

### `GET /api/regulations/{season}`
```json
{"season": 1, "version": "S1", "regulations": { /* Regulations.to_dict() */ },
 "changes": [{"season": 2, "parameter": "compounds.soft.wear_rate", "label": "Usure des pneus tendres",
              "old": 0.168, "new": 0.181, "percent": 7.7, "rationale": "...", "bench": {...}}]}
```

### `GET /api/live` · `WS /ws/live`
`GET /api/live` → `{"live": false}` ou `{"live": true, "round_id": 12, "session": "race", "t": 312.4}`.
Le WebSocket `/ws/live` envoie d'abord `{"type": "init", "replay": {...sans frames...}}` puis des messages
`{"type": "frame", "frame": [t, [...], ...]}` en temps réel, `{"type": "event", ...}` et `{"type": "end"}`.

## 2. Espace écurie (jeton requis)

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/me` | Écurie, pilotes, budget, quota |
| GET | `/api/garage/catalog` | Pièces, coûts du niveau suivant, technologies, révisions nécessaires |
| POST | `/api/garage/upgrade` | `{"part": "engine"}` |
| POST | `/api/garage/research` | `{"technology": "active_cooling"}` |
| POST | `/api/garage/revise` | `{"part": "engine"}` |
| GET | `/api/brains` | Générations de l'écurie |
| POST | `/api/brains/import` | Import d'un paquet cerveau (si le règlement l'autorise) |
| POST | `/api/brains/{id}/rating` | Lance le Driver Rating |
| POST | `/api/drivers/{id}/activate` | `{"brain_id": 41}` |
| POST | `/api/lab/jobs` | Crée un entraînement (voir guide du directeur) |
| GET | `/api/lab/jobs` · `/api/lab/jobs/{id}` | Suivi |
| DELETE | `/api/lab/jobs/{id}` | Annulation |
| POST | `/api/rounds/{id}/entries` | `{"driver_id": "...", "brain_id": 41, "setup": {...}}` |

## 3. Commissaires (jeton admin)

| Méthode | Route | Rôle |
|---|---|---|
| POST | `/api/admin/teams` | Crée une écurie et renvoie son jeton |
| POST | `/api/admin/seasons` | Crée la saison suivante (applique le changement réglementaire) |
| POST | `/api/admin/rounds/{id}/open` | Ouvre les inscriptions |
| POST | `/api/admin/rounds/{id}/run` | Court qualifications + course |
