// Types du contrat d'API v1 (docs/API.md) et du format de replay raicing-replay/1.

export type SeasonStatus = "planned" | "active" | "finished";
export type RoundStatus = "scheduled" | "entries_open" | "running" | "done";
export type Category = "rapide" | "mixte" | "technique";
export type Compound = "soft" | "medium" | "hard" | "wet";
export type RaceStatus = "finished" | "dnf" | "dsq";

// ---------------------------------------------------------------- ligue / saisons
export interface SeasonRef {
  number: number;
  status: SeasonStatus;
  rounds: number;
  rounds_done: number;
}

export interface League {
  name: string;
  current_season: number;
  seasons: SeasonRef[];
}

export interface DriverRef {
  driver_id: string;
  driver: string;
  team_id?: string;
  team?: string;
  color?: string;
}

export interface RoundSummary {
  id: number;
  number: number;
  status: RoundStatus;
  track_name: string | null;
  track_length: number;
  laps: number;
  category: Category;
  rain_probability: number;
  winner: DriverRef | null;
  pole: DriverRef | null;
  date: string | null;
}

export interface RegulationChange {
  season: number;
  parameter: string;
  label: string;
  old: number;
  new: number;
  percent: number;
  rationale?: string;
  affects?: string | null;
  bench?: Record<string, unknown>;
}

export interface Season {
  number: number;
  status: SeasonStatus;
  budget: number;
  quota: number;
  regulation_change: RegulationChange | null;
  rounds: RoundSummary[];
}

export interface DriverStanding {
  pos: number;
  driver_id: string;
  driver: string;
  team_id: string;
  team: string;
  color: string;
  points: number;
  wins: number;
  podiums: number;
  dnf: number;
  by_round: (number | null)[];
}

export interface TeamStanding {
  pos: number;
  team_id: string;
  team: string;
  color: string;
  points: number;
  penalties: number;
  by_round: (number | null)[];
}

export interface Standings {
  drivers: DriverStanding[];
  teams: TeamStanding[];
}

// ---------------------------------------------------------------- manche
export interface QualiRow {
  pos: number;
  car?: number;
  entry_id: string;
  driver: string;
  driver_id: string;
  team: string;
  team_id: string;
  time: number | null;
  gap: number | null;
  status: string;
  reason: string;
  compound: Compound | string;
}

export interface RaceRow {
  pos: number;
  car?: number;
  entry_id: string;
  driver: string;
  driver_id: string;
  team: string;
  team_id: string;
  generation?: number | null;
  status: RaceStatus | string;
  reason: string;
  laps: number;
  time: number | null;
  penalty: number;
  gap: string | number | null;
  best_lap: number | null;
  lap_times: number[];
  pits: number;
  points: number;
  end_time?: number | null;
}

export interface Incident {
  t: number;
  cars: string[];
  kind: "rear" | "side" | "brake_check" | string;
  fault: string | null;
  closing?: number;
  impulse?: number;
  penalised: boolean;
}

export interface FastestLap {
  entry_id: string;
  driver: string;
  time: number;
}

export interface RoundEntry {
  entry_id: string;
  driver_id: string;
  team_id: string;
  brain: { id: number; generation: number; method: string } | null;
  setup: {
    wing: number;
    brake_bias: number;
    start_compound: Compound | string;
    quali_compound: Compound | string;
  };
}

export interface Round {
  id: number;
  season: number;
  number: number;
  status: RoundStatus;
  laps: number;
  date?: string | null;
  category?: Category;
  track_length?: number;
  rain_probability?: number;
  track: { name: string; length: number; seed?: number; corners?: number; category: Category } | null;
  weather: { ambient: number; rain: boolean } | null;
  quali: { classification: QualiRow[] } | null;
  race: {
    classification: RaceRow[];
    fastest_lap: FastestLap | null;
    incidents: Incident[];
    team_penalties: Record<string, number>;
    events?: ReplayEvent[];
    grid?: string[];
  } | null;
  entries: RoundEntry[];
  has_replay: { quali: boolean; race: boolean };
}

// ---------------------------------------------------------------- écuries / pilotes
export type PartKey = "engine" | "aero" | "bodywork" | "chassis" | "brakes" | "cooling" | "suspension";

export interface TeamDriver {
  id: string;
  name: string;
  number: number;
  active_brain: { id: number; generation: number } | null;
}

export interface Garage {
  season: number;
  budget: number;
  spent: number;
  available: number;
  parts: Record<PartKey, number>;
  technologies: string[];
  capacity: { volume: [number, number]; thermal: [number, number]; mass_delta: number };
  non_compliant?: Record<string, number>;
}

export interface Team {
  id: string;
  name: string;
  color: string;
  director: string;
  is_system: boolean;
  drivers: TeamDriver[];
  garage: Garage | null;
  results: { round_id: number; round: number; points: number }[];
  quota: { season: number; used: number; total: number } | null;
}

export interface RatingComponents {
  speed: number;
  consistency: number;
  survival: number;
  rain: number;
  tyres: number;
  traffic: number;
}

export interface Generation {
  id: number;
  generation: number;
  method: string;
  created_at: string;
  rating: number | null;
  training_steps: number;
  parent: number | null;
}

export interface Driver {
  id: string;
  name: string;
  team_id: string;
  team: string;
  color: string;
  number: number;
  active_brain: {
    id: number;
    generation: number;
    method: string;
    params: number;
    created_at: string;
  } | null;
  rating: { overall: number; ci: [number, number]; components: RatingComponents } | null;
  generations: Generation[];
  results: { round_id: number; round: number; season: number; pos: number | null; status: string; points: number; grid: number | null }[];
}

// ---------------------------------------------------------------- règlement
export interface CompoundSpec {
  grip: number;
  t_opt: number;
  t_width: number;
  wear_rate: number;
  wear_shape: number;
  wet_loss: number;
  dry_overheat: number;
}

export interface RegulationsData {
  version: string;
  compounds: Record<string, CompoundSpec>;
  car: Record<string, number>;
  track: Record<string, number>;
  race: Record<string, number | boolean | number[]> & { points: number[] };
  aero: Record<string, number>;
  weather: Record<string, number>;
  economy: Record<string, number>;
  training: Record<string, number | boolean>;
}

export interface Regulations {
  season: number;
  version: string;
  regulations: RegulationsData;
  changes: RegulationChange[];
}

// ---------------------------------------------------------------- direct
export type Live = { live: false } | { live: true; round_id: number; session: "race" | "quali"; t: number };

// ---------------------------------------------------------------- replay
export interface ReplayCar {
  car: number;
  entry_id: string;
  team_id: string;
  team: string;
  driver: string;
  driver_id: string;
  color: string;
  generation: number | null;
  kind: string;
}

export interface ReplayTrack {
  name: string;
  length: number;
  centerline: [number, number][];
  half_width: number[];
  slow_drying?: number[];
  pit_entry_s: number;
  pit_exit_s: number;
  seed?: number;
  meta?: { corners?: number; [k: string]: unknown };
}

/** [t, voiture0[], voiture1[], ...] ; chaque voiture suit `frame_fields`. */
export type ReplayFrame = [number, ...number[][]];

export interface ReplayEvent {
  t: number;
  type: "pit_in" | "pit_out" | "contact" | "penalty" | "out" | "dsq" | "finish" | "team_penalty" | string;
  car?: number;
  cars?: number[];
  kind?: string;
  fault?: number | null;
  seconds?: number;
  compound?: string;
  repair?: boolean;
  reason?: string;
  timed?: boolean;
  team?: number | string;
  points?: number;
  victim?: number;
  entry_id?: string;
}

export interface ReplayHeader {
  format: string;
  kind: "race" | "quali" | string;
  laps: number;
  dt: number;
  track: ReplayTrack;
  cars: ReplayCar[];
  frame_fields: string[];
  status_names: Record<string, string>;
  compounds: string[];
  weather?: { times: number[]; rain: number[]; ambient: number; initial_wetness?: number };
  weather_frames?: [number, number, number[]][];
  events?: ReplayEvent[];
}

export interface Replay extends ReplayHeader {
  frames: ReplayFrame[];
}
