// Couche de données côté serveur. Consomme l'API de la ligue (docs/API.md) ou, si
// RAICING_MOCK=1 ou si RAICING_API_URL n'est pas défini, la ligue de démonstration (lib/mock.ts).
import { gunzipSync } from "node:zlib";
import { readFile } from "node:fs/promises";
import path from "node:path";
import * as mock from "./mock";
import type {
  Driver,
  League,
  Live,
  Regulations,
  Replay,
  Round,
  Season,
  Standings,
  Team,
} from "./types";

export class NotFoundError extends Error {
  constructor(what: string) {
    super(`Introuvable : ${what}`);
  }
}

// Lecture dynamique : ces variables doivent être lues à l'exécution (image Docker), pas figées au build.
function env(name: string): string | undefined {
  const v = process.env[name];
  return v && v.trim() !== "" ? v.trim() : undefined;
}

export function isMock(): boolean {
  return env("RAICING_MOCK") === "1" || !env("RAICING_API_URL");
}

/** Site réduit à la page Rencontres (hébergement sans championnat) : RAICING_SITE=rencontres. */
export function matchesOnly(): boolean {
  return env("RAICING_SITE") === "rencontres";
}

/** URL de l'API vue par le serveur Next (ex. http://api:8000). Sans le suffixe /api. */
export function serverApiUrl(): string | undefined {
  return env("RAICING_API_URL")?.replace(/\/+$/, "").replace(/\/api$/, "");
}

/** URL publique de l'API vue par le navigateur (WebSocket du direct). */
export function publicApiUrl(): string | null {
  const key = ["NEXT", "PUBLIC", "RAICING", "API", "URL"].join("_");
  const v = env(key);
  return v ? v.replace(/\/+$/, "").replace(/\/api$/, "") : null;
}

async function get<T>(route: string, revalidate = 15): Promise<T> {
  const base = serverApiUrl();
  const res = await fetch(`${base}/api${route}`, {
    next: { revalidate },
    headers: { Accept: "application/json" },
  });
  if (res.status === 404) throw new NotFoundError(route);
  if (!res.ok) throw new Error(`API ${route} : HTTP ${res.status}`);
  return (await res.json()) as T;
}

function fromMock<T>(fn: () => T): Promise<T> {
  try {
    return Promise.resolve(fn());
  } catch (e) {
    if (e instanceof mock.MockNotFound) return Promise.reject(new NotFoundError(e.message));
    return Promise.reject(e);
  }
}

export function getLeague(): Promise<League> {
  return isMock() ? fromMock(mock.getLeague) : get<League>("/league", 30);
}

export function getSeason(n: number): Promise<Season> {
  return isMock() ? fromMock(() => mock.getSeason(n)) : get<Season>(`/seasons/${n}`);
}

export function getStandings(n: number): Promise<Standings> {
  return isMock() ? fromMock(() => mock.getStandings(n)) : get<Standings>(`/seasons/${n}/standings`);
}

export function getRound(id: number): Promise<Round> {
  return isMock() ? fromMock(() => mock.getRound(id)) : get<Round>(`/rounds/${id}`);
}

export function getTeams(): Promise<Team[]> {
  return isMock() ? fromMock(mock.getTeams) : get<Team[]>("/teams", 60);
}

export function getTeam(id: string): Promise<Team> {
  return isMock() ? fromMock(() => mock.getTeam(id)) : get<Team>(`/teams/${encodeURIComponent(id)}`, 60);
}

export function getDriver(id: string): Promise<Driver> {
  return isMock() ? fromMock(() => mock.getDriver(id)) : get<Driver>(`/drivers/${encodeURIComponent(id)}`, 60);
}

export function getRegulations(season: number): Promise<Regulations> {
  return isMock() ? fromMock(() => mock.getRegulations(season)) : get<Regulations>(`/regulations/${season}`, 300);
}

export async function getLive(): Promise<Live> {
  if (isMock()) return mock.getLive();
  try {
    const base = serverApiUrl();
    const res = await fetch(`${base}/api/live`, { cache: "no-store" });
    if (!res.ok) return { live: false };
    return (await res.json()) as Live;
  } catch {
    return { live: false };
  }
}

// ------------------------------------------------------------------ replays
export function fixturePath(file: string): string {
  // RAICING_MOCK_REPLAY : chemin d'un autre replay .json.gz à servir en mode maquette (tests).
  if (file === "replay-sample.json.gz" && env("RAICING_MOCK_REPLAY")) return env("RAICING_MOCK_REPLAY")!;
  return path.join(process.cwd(), "fixtures", file);
}

/** Corps brut du replay (gzip ou JSON) tel qu'il doit être relayé au navigateur. */
export async function getReplayResponse(id: number, session: "race" | "quali"): Promise<Response> {
  if (isMock()) {
    const file = mock.mockReplayFile(id, session);
    if (!file) return new Response("Replay introuvable", { status: 404 });
    const buf = await readFile(fixturePath(file));
    return new Response(new Uint8Array(buf), {
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Encoding": "gzip",
        "Cache-Control": "public, max-age=3600",
      },
    });
  }
  const base = serverApiUrl();
  const upstream = await fetch(`${base}/api/rounds/${id}/replay?session=${session}`, { cache: "no-store" });
  if (!upstream.ok) return new Response("Replay introuvable", { status: upstream.status });
  // fetch (undici) décompresse déjà les réponses Content-Encoding: gzip ; un fichier .json.gz
  // servi tel quel (application/gzip) reste compressé et sera décompressé par le navigateur.
  return new Response(upstream.body, {
    headers: {
      "Content-Type": upstream.headers.get("content-type") ?? "application/json",
      "Cache-Control": "public, max-age=300",
    },
  });
}

/** Replay décodé (usage serveur). */
export async function getReplay(id: number, session: "race" | "quali" = "race"): Promise<Replay> {
  if (isMock()) {
    const file = mock.mockReplayFile(id, session);
    if (!file) throw new NotFoundError(`replay ${id}/${session}`);
    const buf = await readFile(fixturePath(file));
    return JSON.parse(gunzipSync(buf).toString("utf8")) as Replay;
  }
  const base = serverApiUrl();
  const res = await fetch(`${base}/api/rounds/${id}/replay?session=${session}`, { cache: "no-store" });
  if (res.status === 404) throw new NotFoundError(`replay ${id}/${session}`);
  if (!res.ok) throw new Error(`API replay : HTTP ${res.status}`);
  const bytes = new Uint8Array(await res.arrayBuffer());
  const raw = bytes[0] === 0x1f && bytes[1] === 0x8b ? gunzipSync(bytes) : Buffer.from(bytes);
  return JSON.parse(raw.toString("utf8")) as Replay;
}

/** Helpers pour les pages : renvoie null au lieu de lever NotFoundError. */
export async function orNull<T>(p: Promise<T>): Promise<T | null> {
  try {
    return await p;
  } catch (e) {
    if (e instanceof NotFoundError) return null;
    throw e;
  }
}
