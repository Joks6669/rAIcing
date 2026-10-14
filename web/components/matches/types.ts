// Rencontre telle que renvoyée par l'API (GET /api/matches/{id}).
export interface MatchEntrant {
  name: string;
  color: string;
  start: string;
  kind: "brain" | "reference";
  reference?: string;
  brain?: { params: number; outputs: number; can_pit: boolean; radio: boolean; method?: string | null; layers: number };
}

export interface MatchRaceRow {
  pos: number;
  car: number;
  driver: string;
  status: string;
  reason: string;
  laps: number;
  time: number | null;
  gap: string | null;
  best_lap: number | null;
  pits: number;
  penalty: number;
}

export interface MatchQualiRow {
  pos: number;
  car: number;
  driver: string;
  time: number | null;
  gap: number | null;
  status: string;
  reason: string;
  compound: string;
}

export interface MatchMeta {
  id: string;
  name: string;
  created_at: string;
  status: "queued" | "running" | "done" | "failed";
  progress: number;
  stage: string;
  track: { seed: number; name: string; length: number; corners: number };
  laps: number;
  weather: string;
  entrants: MatchEntrant[];
  error: string | null;
  results: {
    quali: MatchQualiRow[];
    race: MatchRaceRow[];
    grid: number[];
    weather: { ambient: number; rain: boolean };
    starts: Record<string, string>;
  } | null;
}
