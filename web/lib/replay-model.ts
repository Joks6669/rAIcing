// Modèle de replay côté navigateur : géométrie du circuit, interpolation des images,
// distances parcourues (pour les écarts) et événements. Accepte des images ajoutées en flux (direct).
import type { ReplayEvent, ReplayFrame, ReplayHeader, ReplayTrack } from "./types";

export const STATUS = { racing: 0, pit: 1, finished: 2, dnf: 3, dsq: 4 } as const;

// ------------------------------------------------------------------ géométrie
export interface TrackGeom {
  n: number;
  length: number;
  cx: Float64Array;
  cy: Float64Array;
  s: Float64Array; // abscisse curviligne cumulée
  tx: Float64Array; // tangente unitaire
  ty: Float64Array;
  nx: Float64Array; // normale gauche unitaire
  ny: Float64Array;
  hw: Float64Array;
  bbox: { minX: number; maxX: number; minY: number; maxY: number };
}

export function buildTrack(t: ReplayTrack): TrackGeom {
  const n = t.centerline.length;
  const cx = new Float64Array(n);
  const cy = new Float64Array(n);
  const hw = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    cx[i] = t.centerline[i][0];
    cy[i] = t.centerline[i][1];
    hw[i] = t.half_width[i] ?? t.half_width[t.half_width.length - 1] ?? 7;
  }
  const s = new Float64Array(n);
  for (let i = 1; i < n; i++) s[i] = s[i - 1] + Math.hypot(cx[i] - cx[i - 1], cy[i] - cy[i - 1]);
  const closing = Math.hypot(cx[0] - cx[n - 1], cy[0] - cy[n - 1]);
  const length = t.length || s[n - 1] + closing;
  const tx = new Float64Array(n);
  const ty = new Float64Array(n);
  const nx = new Float64Array(n);
  const ny = new Float64Array(n);
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (let i = 0; i < n; i++) {
    const a = (i - 1 + n) % n;
    const b = (i + 1) % n;
    let dx = cx[b] - cx[a];
    let dy = cy[b] - cy[a];
    const l = Math.hypot(dx, dy) || 1;
    dx /= l;
    dy /= l;
    tx[i] = dx;
    ty[i] = dy;
    nx[i] = -dy;
    ny[i] = dx;
    const m = hw[i] + 2;
    minX = Math.min(minX, cx[i] - m);
    maxX = Math.max(maxX, cx[i] + m);
    minY = Math.min(minY, cy[i] - m);
    maxY = Math.max(maxY, cy[i] + m);
  }
  return { n, length, cx, cy, s, tx, ty, nx, ny, hw, bbox: { minX, maxX, minY, maxY } };
}

/** Index du point de ligne médiane le plus proche de l'abscisse s (s modulo la longueur). */
export function indexAtS(g: TrackGeom, s: number): number {
  const L = g.length;
  const ss = ((s % L) + L) % L;
  let lo = 0, hi = g.n - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (g.s[mid] <= ss) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/** Point à l'abscisse s, décalé latéralement (lat > 0 = gauche). */
export function pointAt(g: TrackGeom, s: number, lat: number): { x: number; y: number; tx: number; ty: number; i: number } {
  const i = indexAtS(g, s);
  return { x: g.cx[i] + g.nx[i] * lat, y: g.cy[i] + g.ny[i] * lat, tx: g.tx[i], ty: g.ty[i], i };
}

// ------------------------------------------------------------------ angles
export function wrapPi(a: number): number {
  a = (a + Math.PI) % (2 * Math.PI);
  if (a < 0) a += 2 * Math.PI;
  return a - Math.PI;
}

// ------------------------------------------------------------------ modèle
export interface CarRow {
  car: number;
  pos: number;
  status: number;
  lap: number;
  compound: number;
  wear: number;
  temp: number;
  speed: number;
  damage: number;
  pits: number;
  gapText: string;
  gapValue: number; // pour tri / affichage ; Infinity si inconnu
  finished: boolean;
}

type Listener = () => void;

export class ReplayModel {
  readonly header: ReplayHeader;
  readonly geom: TrackGeom;
  readonly nCars: number;
  readonly F: Record<string, number>;
  readonly live: boolean;
  times: number[] = [];
  frames: number[][][] = [];
  /** distance cumulée parcourue (m) par voiture et par image */
  dist: number[][];
  /** maximum courant de dist (monotone) pour la recherche des écarts */
  distMax: number[][];
  events: ReplayEvent[] = [];
  finishT: (number | null)[];
  outT: (number | null)[];
  ended = false;
  version = 0;
  private lastIdx: Int32Array;
  private listeners = new Set<Listener>();
  private wetTimes: number[] = [];
  private wetCells: Float32Array[] = [];
  readonly anyWet: boolean;

  constructor(header: ReplayHeader, frames: ReplayFrame[] = [], opts: { live?: boolean } = {}) {
    this.header = header;
    this.live = !!opts.live;
    this.geom = buildTrack(header.track);
    this.nCars = header.cars.length;
    const F: Record<string, number> = {};
    (header.frame_fields ?? ["x", "y", "psi", "speed", "status", "compound", "wear", "temp", "lap", "pos", "damage", "steer", "pedal"]).forEach(
      (f, i) => (F[f] = i),
    );
    this.F = F;
    this.dist = Array.from({ length: this.nCars }, () => []);
    this.distMax = Array.from({ length: this.nCars }, () => []);
    this.finishT = Array(this.nCars).fill(null);
    this.outT = Array(this.nCars).fill(null);
    this.lastIdx = new Int32Array(this.nCars).fill(-1);
    let wet = false;
    for (const wf of header.weather_frames ?? []) {
      this.wetTimes.push(wf[0]);
      const arr = Float32Array.from(wf[2] ?? []);
      this.wetCells.push(arr);
      if (!wet) for (let i = 0; i < arr.length; i++) if (arr[i] > 0.02) { wet = true; break; }
      if (wf[1] > 0) wet = true;
    }
    this.anyWet = wet;
    for (const e of header.events ?? []) this.insertEvent(e, false);
    for (const f of frames) this.pushFrame(f);
    this.version++;
  }

  // -------------------------------------------------------------- flux
  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  private emit() {
    this.version++;
    this.listeners.forEach((l) => l());
  }
  appendFrame(f: ReplayFrame) {
    if (this.times.length && f[0] <= this.times[this.times.length - 1]) return;
    this.pushFrame(f);
    this.emit();
  }
  addEvent(e: ReplayEvent) {
    this.insertEvent(e, true);
    this.emit();
  }
  markEnded() {
    this.ended = true;
    this.emit();
  }

  private insertEvent(e: ReplayEvent, dedupe: boolean) {
    if (dedupe && this.events.some((x) => x.t === e.t && x.type === e.type && x.car === e.car)) return;
    let i = this.events.length;
    while (i > 0 && this.events[i - 1].t > e.t) i--;
    this.events.splice(i, 0, e);
    if (e.type === "finish" && e.car != null && this.finishT[e.car] == null) this.finishT[e.car] = e.t;
    if ((e.type === "out" || e.type === "dsq") && e.car != null && this.outT[e.car] == null) this.outT[e.car] = e.t;
  }

  private pushFrame(f: ReplayFrame) {
    const t = f[0];
    const cars = f.slice(1) as number[][];
    const k = this.times.length;
    this.times.push(t);
    this.frames.push(cars);
    const F = this.F;
    const L = this.geom.length;
    for (let c = 0; c < this.nCars; c++) {
      const d = cars[c];
      const distArr = this.dist[c];
      const maxArr = this.distMax[c];
      if (!d) {
        const prev = k > 0 ? distArr[k - 1] : 0;
        distArr.push(prev);
        maxArr.push(k > 0 ? maxArr[k - 1] : prev);
        continue;
      }
      const st = d[F.status];
      let D: number;
      if (k === 0) {
        const s = this.project(c, d[F.x], d[F.y]);
        const lap = d[F.lap] ?? 0;
        D = lap * L + s;
        if (lap < 0 && s < L / 2) D += L; // déjà au-delà de la ligne
      } else if (st === STATUS.racing) {
        const s = this.project(c, d[F.x], d[F.y]);
        const prev = distArr[k - 1];
        D = s + L * Math.round((prev - s) / L);
      } else {
        D = distArr[k - 1];
      }
      distArr.push(D);
      maxArr.push(k > 0 ? Math.max(maxArr[k - 1], D) : D);
      if (st === STATUS.finished && this.finishT[c] == null) this.finishT[c] = t;
      if ((st === STATUS.dnf || st === STATUS.dsq) && this.outT[c] == null) this.outT[c] = t;
    }
  }

  /** Projection d'une position sur la ligne médiane -> abscisse s. Recherche locale puis globale. */
  private project(c: number, x: number, y: number): number {
    const g = this.geom;
    const n = g.n;
    let best = -1;
    let bestD = Infinity;
    const li = this.lastIdx[c];
    if (li >= 0) {
      for (let o = -25; o <= 60; o++) {
        const i = (li + o + n) % n;
        const dx = g.cx[i] - x;
        const dy = g.cy[i] - y;
        const dd = dx * dx + dy * dy;
        if (dd < bestD) {
          bestD = dd;
          best = i;
        }
      }
    }
    if (best < 0 || bestD > 900) {
      bestD = Infinity;
      for (let i = 0; i < n; i++) {
        const dx = g.cx[i] - x;
        const dy = g.cy[i] - y;
        const dd = dx * dx + dy * dy;
        if (dd < bestD) {
          bestD = dd;
          best = i;
        }
      }
    }
    this.lastIdx[c] = best;
    // affinage sur le segment suivant ou précédent
    const along = (x - g.cx[best]) * g.tx[best] + (y - g.cy[best]) * g.ty[best];
    let s = g.s[best] + along;
    const L = g.length;
    s = ((s % L) + L) % L;
    return s;
  }

  // -------------------------------------------------------------- temps
  get t0(): number {
    return this.times.length ? this.times[0] : 0;
  }
  get tEnd(): number {
    return this.times.length ? this.times[this.times.length - 1] : 0;
  }
  get frameCount(): number {
    return this.times.length;
  }

  /** Plus grand k tel que times[k] <= t. */
  indexAt(t: number): number {
    const T = this.times;
    if (!T.length) return -1;
    if (t <= T[0]) return 0;
    if (t >= T[T.length - 1]) return T.length - 1;
    // images régulières : estimation directe puis correction
    const dt = this.header.dt || 0.2;
    let k = Math.min(T.length - 1, Math.max(0, Math.floor((t - T[0]) / dt)));
    while (k > 0 && T[k] > t) k--;
    while (k < T.length - 1 && T[k + 1] <= t) k++;
    return k;
  }

  /** Pose interpolée d'une voiture (x, y, psi) au temps t. */
  pose(c: number, t: number): { x: number; y: number; psi: number; status: number; k: number } | null {
    const k = this.indexAt(t);
    if (k < 0) return null;
    const F = this.F;
    const a = this.frames[k][c];
    if (!a) return null;
    const b = k + 1 < this.frames.length ? this.frames[k + 1][c] : null;
    const st = a[F.status];
    if (!b || b[F.status] !== st && !(st === STATUS.racing && b[F.status] === STATUS.finished)) {
      return { x: a[F.x], y: a[F.y], psi: a[F.psi], status: st, k };
    }
    const dx = b[F.x] - a[F.x];
    const dy = b[F.y] - a[F.y];
    if (dx * dx + dy * dy > 40 * 40) return { x: a[F.x], y: a[F.y], psi: a[F.psi], status: st, k }; // saut (stands)
    const span = this.times[k + 1] - this.times[k] || 1;
    const f = Math.max(0, Math.min(1, (t - this.times[k]) / span));
    return {
      x: a[F.x] + dx * f,
      y: a[F.y] + dy * f,
      psi: a[F.psi] + wrapPi(b[F.psi] - a[F.psi]) * f,
      status: st,
      k,
    };
  }

  field(c: number, k: number, name: string): number {
    const row = this.frames[k]?.[c];
    const i = this.F[name];
    return row && i != null ? row[i] : NaN;
  }

  // -------------------------------------------------------------- météo
  /** Humidité par cellule de 10 m au temps t (null si piste sèche tout au long). */
  wetnessAt(t: number): { cells: Float32Array; rain: number } | null {
    if (!this.anyWet || !this.wetTimes.length) return null;
    const T = this.wetTimes;
    const wf = this.header.weather_frames!;
    if (t <= T[0]) return { cells: this.wetCells[0], rain: wf[0][1] };
    if (t >= T[T.length - 1]) return { cells: this.wetCells[T.length - 1], rain: wf[T.length - 1][1] };
    let i = 0;
    while (i < T.length - 2 && T[i + 1] <= t) i++;
    const f = (t - T[i]) / (T[i + 1] - T[i] || 1);
    const a = this.wetCells[i];
    const b = this.wetCells[i + 1];
    const out = new Float32Array(a.length);
    for (let j = 0; j < a.length; j++) out[j] = a[j] + ((b[j] ?? a[j]) - a[j]) * f;
    return { cells: out, rain: wf[i][1] + (wf[i + 1][1] - wf[i][1]) * f };
  }

  // -------------------------------------------------------------- classement instantané
  pitsUntil(c: number, t: number): number {
    let n = 0;
    for (const e of this.events) {
      if (e.t > t) break;
      if (e.type === "pit_in" && e.car === c) n++;
    }
    return n;
  }

  penaltyUntil(c: number, t: number): number {
    let s = 0;
    for (const e of this.events) {
      if (e.t > t) break;
      if (e.type === "penalty" && e.car === c) s += e.seconds ?? 0;
    }
    return s;
  }

  /** Temps auquel le leader a atteint la distance D (interpolé), via distMax. */
  private timeAtDistance(leader: number, D: number, kMax: number): number | null {
    const arr = this.distMax[leader];
    if (!arr.length || arr[0] > D) return null;
    let lo = 0, hi = kMax;
    if (arr[hi] < D) return null;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (arr[mid] >= D) hi = mid;
      else lo = mid + 1;
    }
    if (lo === 0) return this.times[0];
    const d0 = arr[lo - 1];
    const d1 = arr[lo];
    const f = d1 > d0 ? (D - d0) / (d1 - d0) : 1;
    return this.times[lo - 1] + f * (this.times[lo] - this.times[lo - 1]);
  }

  rowsAt(t: number): CarRow[] {
    const k = this.indexAt(t);
    if (k < 0) return [];
    const F = this.F;
    const L = this.geom.length;
    const frame = this.frames[k];
    const rows: CarRow[] = [];
    let leader = 0;
    for (let c = 0; c < this.nCars; c++) if (frame[c] && frame[c][F.pos] === 0) leader = c;
    const tk = this.times[k];
    // vainqueur (premier arrivé) pour les écarts à l'arrivée
    let winner = -1;
    let winnerT = Infinity;
    for (let c = 0; c < this.nCars; c++) {
      const ft = this.finishT[c];
      if (ft != null && ft <= t && ft < winnerT) {
        winnerT = ft;
        winner = c;
      }
    }
    for (let c = 0; c < this.nCars; c++) {
      const d = frame[c];
      if (!d) continue;
      const st = d[F.status];
      const row: CarRow = {
        car: c,
        pos: d[F.pos],
        status: st,
        lap: d[F.lap],
        compound: d[F.compound],
        wear: d[F.wear],
        temp: d[F.temp],
        speed: d[F.speed],
        damage: d[F.damage] ?? 0,
        pits: this.pitsUntil(c, t),
        gapText: "",
        gapValue: Infinity,
        finished: st === STATUS.finished,
      };
      if (st === STATUS.dnf || st === STATUS.dsq) {
        row.gapText = "—";
      } else if (st === STATUS.pit) {
        // the distance is frozen at the pit entry while the engine keeps advancing the car's virtual
        // progress (which drives its position): a time gap would contradict the position shown
        row.gapText = "—";
      } else if (st === STATUS.finished && winner >= 0) {
        const lapsDown = this.field(winner, k, "lap") - d[F.lap];
        if (c === winner) row.gapText = "Vainqueur";
        else if (lapsDown >= 1) row.gapText = `+${lapsDown} ${lapsDown > 1 ? "tours" : "tour"}`;
        else {
          const g = this.finishT[c]! + this.penaltyUntil(c, t) - (winnerT + this.penaltyUntil(winner, t));
          row.gapValue = g;
          row.gapText = `+${g.toFixed(1)}\u00a0s`;
        }
      } else if (c === leader) {
        row.gapText = "Tête";
        row.gapValue = 0;
      } else {
        const Dc = this.dist[c][k];
        const DL = this.dist[leader][k];
        const lapsDown = Math.floor((DL - Dc) / L);
        if (lapsDown >= 1) {
          row.gapText = `+${lapsDown} ${lapsDown > 1 ? "tours" : "tour"}`;
        } else {
          const tl = this.timeAtDistance(leader, Dc, k);
          const vL = frame[leader][F.speed] ?? 0;
          if (tl != null) {
            const g = Math.max(0, tk - tl);
            row.gapValue = g;
            row.gapText = `+${g.toFixed(1)}\u00a0s`;
          } else if (vL > 5) {
            // juste après le départ : estimation distance / vitesse du leader
            const g = Math.max(0, (DL - Dc) / vL);
            row.gapValue = g;
            row.gapText = `+${g.toFixed(1)}\u00a0s`;
          } else {
            row.gapText = "—";
          }
        }
      }
      rows.push(row);
    }
    rows.sort((a, b) => a.pos - b.pos);
    return rows;
  }

  /** Tour en cours du leader (1-indexé), borné au nombre de tours. */
  leaderLap(t: number): { lap: number; total: number; phase: "grid" | "race" | "finish" } {
    const k = this.indexAt(t);
    const total = this.header.laps;
    if (k < 0) return { lap: 0, total, phase: "grid" };
    const F = this.F;
    let maxLap = -1;
    for (const d of this.frames[k]) if (d && d[F.lap] > maxLap) maxLap = d[F.lap];
    const anyFinished = this.finishT.some((ft) => ft != null && ft <= t);
    if (anyFinished) return { lap: total, total, phase: "finish" };
    if (maxLap < 0) return { lap: 0, total, phase: "grid" };
    return { lap: Math.min(total, maxLap + 1), total, phase: "race" };
  }

  /** Série temporelle d'un champ pour une voiture, sous-échantillonnée à au plus maxPoints. */
  series(c: number, name: string, maxPoints = 900): { t: number[]; v: number[]; compound: number[] } {
    const i = this.F[name];
    const ci = this.F.compound;
    const si = this.F.status;
    const n = this.frames.length;
    const step = Math.max(1, Math.ceil(n / maxPoints));
    const t: number[] = [];
    const v: number[] = [];
    const comp: number[] = [];
    for (let k = 0; k < n; k += step) {
      const d = this.frames[k][c];
      if (!d) continue;
      const st = d[si];
      if (st === STATUS.dnf || st === STATUS.dsq) break;
      t.push(this.times[k]);
      v.push(st === STATUS.pit ? NaN : d[i]);
      comp.push(d[ci]);
      if (st === STATUS.finished) break;
    }
    return { t, v, compound: comp };
  }
}
