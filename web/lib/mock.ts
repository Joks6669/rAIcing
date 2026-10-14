// Ligue de démonstration construite à partir des fixtures réelles (web/fixtures/).
// Manche 1 = vrais résultats (qualifications + course) et vrai replay ; manches 2 à 10 programmées.
// Les éléments absents des fixtures (directeurs, garages, générations, Driver Rating) sont
// générés de façon déterministe et plausible, avec les règles de raicing/league/economy.py.
import sample from "@/fixtures/round-sample.json";
import regulationsDefault from "./regulations-default.json";
import type {
  Category,
  Driver,
  DriverStanding,
  Garage,
  Generation,
  Incident,
  League,
  Live,
  PartKey,
  QualiRow,
  RaceRow,
  Regulations,
  RegulationsData,
  ReplayEvent,
  Round,
  RoundSummary,
  Season,
  Standings,
  Team,
  TeamStanding,
} from "./types";

export class MockNotFound extends Error {}

const SEASON = 1;
const ROUNDS = 10;
const BUDGET = 1000;
const QUOTA = 40_000_000;

// ------------------------------------------------------------------ fixture
type FixtureCar = { car: number; entry_id: string; team_id: string; team: string; driver: string; driver_id: string; color: string };
const fx = sample as unknown as {
  quali: { classification: QualiRow[]; cars: FixtureCar[] };
  race: {
    classification: RaceRow[];
    fastest_lap: { entry_id: string; driver: string; time: number };
    incidents: Incident[];
    events: ReplayEvent[];
    team_penalties: Record<string, number>;
    grid: string[];
    laps: number;
    cars: FixtureCar[];
  };
};

const ROUND1_TRACK = { name: "Circuit de Valnoue", length: 3261.61, seed: 7, corners: 5, category: "rapide" as Category };
const ROUND1_WEATHER = { ambient: 22.0, rain: false };

// ------------------------------------------------------------------ PRNG déterministe
function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
function rng(seed: string) {
  let a = hash(seed);
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const round1 = (x: number) => Math.round(x * 10) / 10;
const clamp = (x: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, x));

// ------------------------------------------------------------------ économie (economy.py)
const PART_SPEC: Record<PartKey, { base: number; volume: number; thermal: number; mass: number }> = {
  engine: { base: 70, volume: 1.0, thermal: 2.0, mass: 2.5 },
  aero: { base: 65, volume: 1.5, thermal: 0.0, mass: 1.0 },
  bodywork: { base: 55, volume: 1.0, thermal: 1.0, mass: 0.5 },
  chassis: { base: 60, volume: 0.0, thermal: 0.0, mass: -4.0 },
  brakes: { base: 40, volume: 0.5, thermal: 1.0, mass: 1.0 },
  cooling: { base: 45, volume: 1.0, thermal: 0.0, mass: 1.5 },
  suspension: { base: 50, volume: 0.5, thermal: 0.0, mass: 1.0 },
};
const TECH_COST: Record<string, number> = {
  composites: 120,
  active_cooling: 110,
  low_drag: 100,
  carbon_brakes: 90,
  tyre_telemetry: 80,
  energy_recovery: 150,
};
const gain = (level: number, maxGain: number) =>
  (maxGain * (1 - Math.exp(-level / 3.5))) / (1 - Math.exp(-10 / 3.5));

function buildGarage(parts: Record<PartKey, number>, tech: string[]): Garage {
  let spent = 0;
  for (const [p, lv] of Object.entries(parts) as [PartKey, number][]) {
    for (let l = 0; l < lv; l++) spent += Math.round(PART_SPEC[p].base * 1.35 ** l * 10) / 10;
  }
  for (const t of tech) spent += TECH_COST[t] ?? 0;
  const volCap = 12 + 2 * parts.chassis + (tech.includes("composites") ? 3 : 0);
  const thCap = 8 + 2 * parts.cooling + (tech.includes("active_cooling") ? 4 : 0);
  let vol = 0;
  let th = tech.includes("energy_recovery") ? 1 : 0;
  let mass = 0;
  for (const [p, lv] of Object.entries(parts) as [PartKey, number][]) {
    vol += PART_SPEC[p].volume * lv;
    th += PART_SPEC[p].thermal * lv;
    if (p !== "chassis") mass += PART_SPEC[p].mass * lv;
  }
  mass -= 30 * gain(parts.chassis, 1) * (tech.includes("composites") ? 1.3 : 1);
  spent = Math.round(spent * 10) / 10;
  return {
    season: SEASON,
    budget: BUDGET,
    spent,
    available: Math.round((BUDGET - spent) * 10) / 10,
    parts,
    technologies: tech,
    capacity: { volume: [vol, volCap], thermal: [th, thCap], mass_delta: Math.round(mass * 100) / 100 },
  };
}

// ------------------------------------------------------------------ écuries
interface TeamMeta {
  director: string;
  is_system: boolean;
  parts: Record<PartKey, number>;
  tech: string[];
  numbers: [number, number];
  rating: [number, number];
  methods: string[];
}

const P = (engine: number, aero: number, bodywork: number, chassis: number, brakes: number, cooling: number, suspension: number) => ({
  engine, aero, bodywork, chassis, brakes, cooling, suspension,
});

const TEAM_META: Record<string, TeamMeta> = {
  astra: { director: "Astra 6", is_system: false, parts: P(3, 3, 1, 2, 2, 1, 0), tech: ["tyre_telemetry"], numbers: [7, 21], rating: [71.8, 69.6], methods: ["bc", "ppo", "ppo", "sac", "sac", "sac", "sac"] },
  lynx: { director: "Lynx 3", is_system: false, parts: P(3, 2, 2, 2, 1, 2, 2), tech: [], numbers: [4, 44], rating: [68.4, 66.9], methods: ["bc", "es", "es", "es", "ppo", "ppo"] },
  aurora: { director: "Aurora 4", is_system: false, parts: P(2, 3, 1, 3, 2, 1, 1), tech: [], numbers: [11, 16], rating: [63.5, 61.2], methods: ["ppo", "ppo", "ppo", "td3", "td3"] },
  ember: { director: "Ember 2", is_system: false, parts: P(4, 1, 1, 1, 1, 3, 0), tech: ["active_cooling"], numbers: [3, 33], rating: [62.2, 59.8], methods: ["bc", "sac", "sac", "sac"] },
  boreal: { director: "Boréal 5", is_system: false, parts: P(2, 2, 2, 2, 2, 1, 2), tech: [], numbers: [9, 27], rating: [55.4, 53.7], methods: ["ppo", "ppo", "es", "es", "es", "ppo"] },
  ondine: { director: "Ondine 3", is_system: false, parts: P(2, 2, 0, 2, 1, 1, 3), tech: ["tyre_telemetry"], numbers: [12, 88], rating: [52.6, 54.1], methods: ["bc", "td3", "td3"] },
  vulcain: { director: "Système de la ligue", is_system: true, parts: P(2, 1, 1, 1, 1, 1, 1), tech: [], numbers: [5, 55], rating: [47.6, 46.2], methods: [] },
  cobalt: { director: "Système de la ligue", is_system: true, parts: P(1, 1, 1, 1, 1, 1, 1), tech: [], numbers: [2, 22], rating: [41.3, 39.7], methods: [] },
};

const CARS = fx.race.cars;
const TEAM_ORDER = ["astra", "lynx", "aurora", "ember", "boreal", "ondine", "vulcain", "cobalt"];

function teamInfo(teamId: string) {
  const c = CARS.find((x) => x.team_id === teamId);
  if (!c) throw new MockNotFound(teamId);
  return { id: teamId, name: c.team, color: c.color };
}

// ------------------------------------------------------------------ générations & ratings
const SEASON_START = Date.parse("2026-09-01T08:00:00Z");
const ROUND1_DATE = Date.parse("2026-10-10T16:00:00Z");

interface DriverGen {
  gens: Generation[];
  params: number;
  rating: NonNullable<Driver["rating"]>;
}

let brainCounter = 100;
const DRIVER_GENS: Record<string, DriverGen> = {};

for (const teamId of TEAM_ORDER) {
  const meta = TEAM_META[teamId];
  for (const k of [0, 1] as const) {
    const driverId = `${teamId}-${k + 1}`;
    const r = rng(driverId);
    const overall = meta.rating[k];
    const comp = (bias: number, spread: number) => round1(clamp(overall + bias + (r() - 0.5) * spread, 5, 99));
    const components = {
      speed: comp(4, 10),
      consistency: comp(0, 18),
      survival: comp(2, 22),
      rain: comp(-6, 26),
      tyres: comp(-1, 20),
      traffic: comp(1, 18),
    };
    const half = round1(1.4 + r() * 2.2);
    const gens: Generation[] = [];
    // les pilotes d'écuries système sont des pilotes de référence : pas de génération
    const n = meta.methods.length;
    let parent: number | null = null;
    for (let g = 0; g < n; g++) {
      const id = ++brainCounter;
      const progress = n === 1 ? 1 : g / (n - 1);
      const created = SEASON_START + (ROUND1_DATE - 2 * 86400000 - SEASON_START) * (0.08 + 0.92 * progress) + r() * 3600000 * 6;
      const method = meta.methods[g];
      const steps = method === "bc" ? Math.round((0.4 + r() * 0.6) * 1e6 / 1e4) * 1e4 : Math.round((1.2 + r() * 2.6) * 1e6 / 1e4) * 1e4;
      const rating = g === n - 1 ? overall : round1(overall - (1 - progress) * (18 + r() * 10) - r() * 2);
      gens.push({
        id,
        generation: g + 1,
        method,
        created_at: new Date(created).toISOString(),
        rating: g > 0 && g < n - 1 && r() < 0.2 ? null : rating,
        training_steps: steps,
        parent: method === "bc" || g === 0 ? null : parent,
      });
      parent = id;
    }
    const params = [52_000, 86_000, 140_000, 210_000, 330_000][Math.floor(r() * 5)];
    DRIVER_GENS[driverId] = { gens, params, rating: { overall, ci: [round1(overall - half), round1(overall + half * 0.9)], components } };
  }
}

function teamQuotaUsed(teamId: string): number {
  let s = 0;
  for (const k of [1, 2]) for (const g of DRIVER_GENS[`${teamId}-${k}`].gens) s += g.training_steps;
  return s;
}

// ------------------------------------------------------------------ calendrier
const FUTURE: { category: Category; length: number }[] = [
  { category: "technique", length: 4120 },
  { category: "mixte", length: 4680 },
  { category: "rapide", length: 5240 },
  { category: "technique", length: 3580 },
  { category: "mixte", length: 3920 },
  { category: "rapide", length: 4410 },
  { category: "mixte", length: 5010 },
  { category: "technique", length: 3360 },
  { category: "rapide", length: 5560 },
];
const AVG_SPEED: Record<Category, number> = { rapide: 55, mixte: 48, technique: 41 };
const RAIN_PROBABILITY = (regulationsDefault as unknown as RegulationsData).weather.rain_probability;

function roundDate(n: number): string {
  // samedis à 18 h (heure de Paris) à partir du 10 octobre 2026
  const d = new Date(Date.UTC(2026, 9, 10 + 7 * (n - 1), 16, 0, 0));
  // passage à l'heure d'hiver le 25 octobre 2026
  if (d.getTime() > Date.UTC(2026, 9, 25, 1)) d.setUTCHours(17);
  return d.toISOString();
}

function driverRef(entryId: string) {
  const c = CARS.find((x) => x.entry_id === entryId)!;
  return { driver_id: c.driver_id, driver: c.driver, team_id: c.team_id, team: c.team, color: c.color };
}

function roundSummaries(): RoundSummary[] {
  const out: RoundSummary[] = [];
  const winner = fx.race.classification.find((r) => r.pos === 1)!;
  const pole = fx.quali.classification.find((r) => r.pos === 1)!;
  out.push({
    id: 1,
    number: 1,
    status: "done",
    track_name: ROUND1_TRACK.name,
    track_length: ROUND1_TRACK.length,
    laps: fx.race.laps,
    category: ROUND1_TRACK.category,
    rain_probability: RAIN_PROBABILITY,
    winner: driverRef(winner.entry_id),
    pole: { driver_id: pole.driver_id, driver: pole.driver },
    date: roundDate(1),
  });
  FUTURE.forEach((f, i) => {
    const n = i + 2;
    const lapTime = f.length / AVG_SPEED[f.category];
    out.push({
      id: n,
      number: n,
      status: n === 2 ? "entries_open" : "scheduled",
      track_name: null,
      track_length: f.length,
      laps: clamp(Math.round(1050 / lapTime), 10, 15),
      category: f.category,
      rain_probability: RAIN_PROBABILITY,
      winner: null,
      pole: null,
      date: roundDate(n),
    });
  });
  return out;
}

// ------------------------------------------------------------------ classements
function computeStandings(): Standings {
  const rows = fx.race.classification;
  const drivers: DriverStanding[] = CARS.map((c) => {
    const r = rows.find((x) => x.entry_id === c.entry_id);
    const pts = r?.points ?? 0;
    const finished = r?.status === "finished";
    return {
      pos: 0,
      driver_id: c.driver_id,
      driver: c.driver,
      team_id: c.team_id,
      team: c.team,
      color: c.color,
      points: pts,
      wins: finished && r!.pos === 1 ? 1 : 0,
      podiums: finished && r!.pos <= 3 ? 1 : 0,
      dnf: r && r.status !== "finished" ? 1 : 0,
      by_round: [pts, ...Array(ROUNDS - 1).fill(null)],
      _best: r?.pos ?? 99,
    } as DriverStanding & { _best: number };
  });
  drivers.sort(
    (a, b) =>
      b.points - a.points || b.wins - a.wins || b.podiums - a.podiums ||
      (a as DriverStanding & { _best: number })._best - (b as DriverStanding & { _best: number })._best,
  );
  drivers.forEach((d, i) => {
    d.pos = i + 1;
    delete (d as Partial<DriverStanding & { _best: number }>)._best;
  });

  const pen = fx.race.team_penalties ?? {};
  const teams: TeamStanding[] = TEAM_ORDER.map((tid) => {
    const info = teamInfo(tid);
    const raw = drivers.filter((d) => d.team_id === tid).reduce((s, d) => s + d.points, 0);
    const p = pen[tid] ?? 0;
    return {
      pos: 0,
      team_id: tid,
      team: info.name,
      color: info.color,
      points: raw - p,
      penalties: p,
      by_round: [raw - p, ...Array(ROUNDS - 1).fill(null)],
    };
  });
  teams.sort((a, b) => b.points - a.points);
  teams.forEach((t, i) => (t.pos = i + 1));
  return { drivers, teams };
}

const STANDINGS = computeStandings();

// ------------------------------------------------------------------ API simulée
export function getLeague(): League {
  return {
    name: "AI Racing League",
    current_season: SEASON,
    seasons: [{ number: SEASON, status: "active", rounds: ROUNDS, rounds_done: 1 }],
  };
}

export function getSeason(n: number): Season {
  if (n !== SEASON) throw new MockNotFound(`season ${n}`);
  return { number: SEASON, status: "active", budget: BUDGET, quota: QUOTA, regulation_change: null, rounds: roundSummaries() };
}

export function getStandings(n: number): Standings {
  if (n !== SEASON) throw new MockNotFound(`season ${n}`);
  return structuredClone(STANDINGS);
}

export function getRound(id: number): Round {
  const summary = roundSummaries().find((r) => r.id === id);
  if (!summary) throw new MockNotFound(`round ${id}`);
  if (id !== 1) {
    return {
      id,
      season: SEASON,
      number: summary.number,
      status: summary.status,
      laps: summary.laps,
      date: summary.date,
      category: summary.category,
      track_length: summary.track_length,
      rain_probability: summary.rain_probability,
      track: null,
      weather: null,
      quali: null,
      race: null,
      entries: [],
      has_replay: { quali: false, race: false },
    };
  }
  const entries = CARS.map((c) => {
    const g = DRIVER_GENS[c.driver_id];
    const last = g.gens[g.gens.length - 1];
    const r = rng(`setup-${c.entry_id}`);
    return {
      entry_id: c.entry_id,
      driver_id: c.driver_id,
      team_id: c.team_id,
      brain: last ? { id: last.id, generation: last.generation, method: last.method } : null,
      setup: {
        wing: Math.round((0.45 + r() * 0.3) * 100) / 100,
        brake_bias: Math.round((0.55 + r() * 0.06) * 100) / 100,
        start_compound: "medium",
        quali_compound: "soft",
      },
    };
  });
  return {
    id: 1,
    season: SEASON,
    number: 1,
    status: "done",
    laps: fx.race.laps,
    date: summary.date,
    category: ROUND1_TRACK.category,
    track_length: ROUND1_TRACK.length,
    rain_probability: RAIN_PROBABILITY,
    track: { ...ROUND1_TRACK },
    weather: { ...ROUND1_WEATHER },
    quali: { classification: structuredClone(fx.quali.classification) },
    race: {
      classification: structuredClone(fx.race.classification),
      fastest_lap: { ...fx.race.fastest_lap },
      incidents: structuredClone(fx.race.incidents),
      team_penalties: { ...(fx.race.team_penalties ?? {}) },
      events: structuredClone(fx.race.events),
      grid: [...fx.race.grid],
    },
    entries,
    has_replay: { quali: false, race: true },
  };
}

export function getTeams(): Team[] {
  return TEAM_ORDER.map((t) => getTeam(t));
}

export function getTeam(id: string): Team {
  const meta = TEAM_META[id];
  if (!meta) throw new MockNotFound(`team ${id}`);
  const info = teamInfo(id);
  const standing = STANDINGS.teams.find((t) => t.team_id === id)!;
  return {
    id,
    name: info.name,
    color: info.color,
    director: meta.director,
    is_system: meta.is_system,
    drivers: [1, 2].map((k) => {
      const did = `${id}-${k}`;
      const car = CARS.find((c) => c.driver_id === did)!;
      const gens = DRIVER_GENS[did].gens;
      const last = gens[gens.length - 1];
      return {
        id: did,
        name: car.driver,
        number: meta.numbers[k - 1],
        active_brain: last ? { id: last.id, generation: last.generation } : null,
      };
    }),
    garage: buildGarage(meta.parts, meta.tech),
    results: [{ round_id: 1, round: 1, points: standing.by_round[0] ?? 0 }],
    quota: { season: SEASON, used: meta.is_system ? 0 : teamQuotaUsed(id), total: QUOTA },
  };
}

export function getDriver(id: string): Driver {
  const car = CARS.find((c) => c.driver_id === id);
  if (!car) throw new MockNotFound(`driver ${id}`);
  const meta = TEAM_META[car.team_id];
  const g = DRIVER_GENS[id];
  const last = g.gens[g.gens.length - 1];
  const row = fx.race.classification.find((r) => r.driver_id === id);
  const gridIdx = fx.race.grid.indexOf(car.entry_id);
  return {
    id,
    name: car.driver,
    team_id: car.team_id,
    team: car.team,
    color: car.color,
    number: meta.numbers[Number(id.split("-").pop()) - 1],
    active_brain: last
      ? { id: last.id, generation: last.generation, method: last.method, params: g.params, created_at: last.created_at }
      : null,
    rating: structuredClone(g.rating),
    generations: [...g.gens].reverse(),
    results: row
      ? [{ round_id: 1, round: 1, season: SEASON, pos: row.pos, status: row.status, points: row.points, grid: gridIdx >= 0 ? gridIdx + 1 : null }]
      : [],
  };
}

export function getRegulations(season: number): Regulations {
  if (season !== SEASON) throw new MockNotFound(`regulations ${season}`);
  return {
    season: SEASON,
    version: "S1",
    regulations: structuredClone(regulationsDefault) as unknown as RegulationsData,
    changes: [],
  };
}

export function getLive(): Live {
  if (process.env.RAICING_MOCK_LIVE === "1") return { live: true, round_id: 1, session: "race", t: 0 };
  return { live: false };
}

/** Chemin du replay de démonstration (gzip) pour une manche, ou null. */
export function mockReplayFile(id: number, session: string): string | null {
  if (id === 1 && session === "race") return "replay-sample.json.gz";
  return null;
}
