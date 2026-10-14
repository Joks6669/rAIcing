// Rendu « retransmission » du replay : circuit urbain de nuit (murs éclairés, vibreurs, damier),
// voitures monoplaces aux couleurs des écuries avec traînées et feux stop, étincelles aux contacts,
// pluie et gerbes d'eau. Caméra « réalisateur » qui suit les bagarres et coupe sur les incidents.
import { STATUS, indexAtS, type ReplayModel, type TrackGeom } from "@/lib/replay-model";
import type { Camera } from "./draw";

const C = {
  bgTop: "#05070c",
  bgBottom: "#0b1220",
  block: "rgba(120,150,200,0.045)",
  blockLit: "rgba(255,196,110,0.06)",
  runoff: "#0c1018",
  asphalt: "#3a404b",
  asphaltHi: "#2f3540",
  racingLine: "rgba(10,12,16,0.35)",
  wall: "#f2f5fa",
  wallGlow: "rgba(170,205,255,0.55)",
  kerbRed: "#e0313b",
  kerbWhite: "#f4f4f4",
  pit: "#ffb43b",
  label: "#ffffff",
  labelBg: "rgba(6,9,15,0.86)",
  grey: "#6b7380",
};

// ------------------------------------------------------------------ décor pré-calculé
export interface Scenery {
  centre: Path2D;
  left: Path2D;
  right: Path2D;
  asphalt: Path2D;
  kerbRed: Path2D;
  kerbWhite: Path2D;
  blocks: { x: number; y: number; w: number; h: number; lit: boolean }[];
  lamps: { x: number; y: number }[];
  cells: Path2D[] | null;
  maxHw: number;
}

function rng(seed: number) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13;
    s ^= s >>> 17;
    s ^= s << 5;
    return (s >>> 0) / 4294967296;
  };
}

export function buildScenery(g: TrackGeom, withCells: boolean, seed = 7): Scenery {
  const n = g.n;
  const centre = new Path2D();
  const left = new Path2D();
  const right = new Path2D();
  for (let i = 0; i <= n; i++) {
    const j = i % n;
    const f = i === 0 ? "moveTo" : "lineTo";
    centre[f](g.cx[j], g.cy[j]);
    left[f](g.cx[j] + g.nx[j] * g.hw[j], g.cy[j] + g.ny[j] * g.hw[j]);
    right[f](g.cx[j] - g.nx[j] * g.hw[j], g.cy[j] - g.ny[j] * g.hw[j]);
  }
  const asphalt = new Path2D();
  asphalt.addPath(left);
  asphalt.addPath(right);

  // vibreurs à l'intérieur des virages (courbure signée par variation de cap)
  const kerbRed = new Path2D();
  const kerbWhite = new Path2D();
  const curv = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const a = (i - 2 + n) % n;
    const b = (i + 2) % n;
    const cross = g.tx[a] * g.ty[b] - g.ty[a] * g.tx[b];
    const ds = Math.max(1, Math.hypot(g.cx[b] - g.cx[a], g.cy[b] - g.cy[a]));
    curv[i] = Math.asin(Math.max(-1, Math.min(1, cross))) / ds;
  }
  const KW = 1.3; // largeur du vibreur (m)
  let stripe = 0;
  for (let i = 0; i < n; i++) {
    const k = curv[i];
    if (Math.abs(k) < 1 / 140) continue;
    const j = (i + 1) % n;
    const side = k > 0 ? 1 : -1; // virage à gauche -> vibreur côté gauche
    const p = stripe++ % 2 === 0 ? kerbRed : kerbWhite;
    const o0 = g.hw[i] * side, o1 = (g.hw[i] - KW) * side;
    const q0 = g.hw[j] * side, q1 = (g.hw[j] - KW) * side;
    p.moveTo(g.cx[i] + g.nx[i] * o0, g.cy[i] + g.ny[i] * o0);
    p.lineTo(g.cx[j] + g.nx[j] * q0, g.cy[j] + g.ny[j] * q0);
    p.lineTo(g.cx[j] + g.nx[j] * q1, g.cy[j] + g.ny[j] * q1);
    p.lineTo(g.cx[i] + g.nx[i] * o1, g.cy[i] + g.ny[i] * o1);
    p.closePath();
  }

  // « ville » : blocs et lampadaires loin de la piste
  const r = rng(seed);
  const { minX, maxX, minY, maxY } = g.bbox;
  const pad = 260;
  let maxHw = 0;
  for (let i = 0; i < n; i++) maxHw = Math.max(maxHw, g.hw[i]);
  const clearOf = (x: number, y: number, margin: number) => {
    for (let i = 0; i < n; i += 2) {
      const dx = g.cx[i] - x, dy = g.cy[i] - y;
      if (dx * dx + dy * dy < (g.hw[i] + margin) ** 2) return false;
    }
    return true;
  };
  const blocks: Scenery["blocks"] = [];
  for (let tries = 0; tries < 900 && blocks.length < 260; tries++) {
    const w = 14 + r() * 40, h = 14 + r() * 40;
    const x = minX - pad + r() * (maxX - minX + 2 * pad);
    const y = minY - pad + r() * (maxY - minY + 2 * pad);
    if (!clearOf(x + w / 2, y + h / 2, 28 + Math.hypot(w, h) / 2)) continue;
    blocks.push({ x, y, w, h, lit: r() < 0.18 });
  }
  const lamps: Scenery["lamps"] = [];
  for (let i = 0; i < n; i += 9) {
    const side = (i / 9) % 2 === 0 ? 1 : -1;
    const off = (g.hw[i] + 6) * side;
    lamps.push({ x: g.cx[i] + g.nx[i] * off, y: g.cy[i] + g.ny[i] * off });
  }

  let cells: Path2D[] | null = null;
  if (withCells) {
    cells = [];
    const cellSize = 10;
    const nCells = Math.ceil(g.length / cellSize);
    for (let j = 0; j < nCells; j++) {
      const a = indexAtS(g, j * cellSize);
      let b = indexAtS(g, Math.min(g.length - 0.01, (j + 1) * cellSize));
      if (b < a) b = n - 1;
      const p = new Path2D();
      const end = Math.min(b + 1, n - 1);
      for (let i = a; i <= end; i++) {
        const x = g.cx[i] + g.nx[i] * g.hw[i], y = g.cy[i] + g.ny[i] * g.hw[i];
        if (i === a) p.moveTo(x, y);
        else p.lineTo(x, y);
      }
      for (let i = end; i >= a; i--) p.lineTo(g.cx[i] - g.nx[i] * g.hw[i], g.cy[i] - g.ny[i] * g.hw[i]);
      p.closePath();
      cells.push(p);
    }
  }
  return { centre, left, right, asphalt, kerbRed, kerbWhite, blocks, lamps, cells, maxHw };
}

// ------------------------------------------------------------------ temps forts (bandeaux)
export type HighlightKind = "overtake" | "pit" | "contact" | "out" | "dsq" | "penalty" | "finish" | "winner" | "fastest" | "start";

export interface Highlight {
  t: number;
  kind: HighlightKind;
  title: string;
  text: string;
  car: number | null;
  other?: number | null;
  /** priorité pour la caméra (plus haut = coupe immédiate) */
  weight: number;
}

const REASON: Record<string, string> = {
  wall: "contact avec le mur",
  broken: "voiture détruite",
  tyre: "pneu détruit",
  puncture: "crevaison",
  stalled: "arrêt prolongé",
  wrong_way: "demi-tour",
  caused_elimination: "a provoqué une élimination",
};

export function buildHighlights(m: ReplayModel): Highlight[] {
  const out: Highlight[] = [];
  const cars = m.header.cars;
  const nm = (c: number | null | undefined) => (c == null ? "?" : (cars[c]?.driver ?? `#${c}`).toUpperCase());
  const F = m.F;
  const race = m.header.kind !== "quali";
  if (race && m.frames.length) out.push({ t: m.t0 + 0.05, kind: "start", title: "DÉPART", text: "Les feux s'éteignent !", car: null, weight: 1 });

  // dépassements : un pilote gagne une place sur un rival en piste (hors stands, abandons)
  if (race) {
    let lastT = -Infinity;
    for (let k = 1; k < m.frames.length; k++) {
      const t = m.times[k];
      if (t - m.t0 < 6) continue; // la mêlée du départ
      const a = m.frames[k - 1], b = m.frames[k];
      for (let c = 0; c < m.nCars; c++) {
        const pa = a[c], pb = b[c];
        if (!pa || !pb || pb[F.status] !== STATUS.racing || pa[F.status] !== STATUS.racing) continue;
        if (pb[F.pos] !== pa[F.pos] - 1 || pb[F.pos] > 9) continue;
        // la voiture passée
        let o = -1;
        for (let d = 0; d < m.nCars; d++) {
          if (d !== c && b[d] && a[d] && a[d][F.pos] === pb[F.pos] && b[d][F.pos] === pa[F.pos]) o = d;
        }
        if (o < 0 || b[o][F.status] !== STATUS.racing || a[o][F.status] !== STATUS.racing) continue;
        if (t - lastT < 2.5) continue;
        lastT = t;
        const p = pb[F.pos] + 1;
        out.push({ t, kind: "overtake", title: p === 1 ? "NOUVEAU LEADER" : "DÉPASSEMENT", text: `${nm(c)} passe ${nm(o)} pour la P${p}`, car: c, other: o, weight: p === 1 ? 6 : 4 });
      }
    }
  }

  let bestLap = Infinity;
  const lapStart: number[] = new Array(m.nCars).fill(NaN);
  if (race) {
    for (let k = 1; k < m.frames.length; k++) {
      for (let c = 0; c < m.nCars; c++) {
        const pa = m.frames[k - 1][c], pb = m.frames[k][c];
        if (!pa || !pb || pb[F.lap] <= pa[F.lap] || pb[F.status] !== STATUS.racing && pb[F.status] !== STATUS.finished) continue;
        const t = m.times[k];
        if (Number.isFinite(lapStart[c]) && pb[F.lap] >= 2) {
          const lt = t - lapStart[c];
          if (lt < bestLap - 0.05 && lt > 10) {
            bestLap = lt;
            const mm = Math.floor(lt / 60), ss = (lt - mm * 60).toFixed(3).padStart(6, "0");
            out.push({ t, kind: "fastest", title: "MEILLEUR TOUR", text: `${nm(c)} — ${mm}:${ss}`, car: c, weight: 2 });
          }
        }
        lapStart[c] = t;
      }
    }
  }

  let winner: number | null = null;
  for (const e of m.events) {
    switch (e.type) {
      case "pit_in":
        out.push({ t: e.t, kind: "pit", title: "ARRÊT AUX STANDS", text: `${nm(e.car)}${e.compound ? ` chausse des ${COMPOUND_FR[e.compound] ?? e.compound}` : ""}`, car: e.car ?? null, weight: 2 });
        break;
      case "contact": {
        const [a, b] = e.cars ?? [];
        const fault = e.fault != null ? ` · fautif : ${nm(e.fault)}` : "";
        out.push({ t: e.t, kind: "contact", title: "CONTACT", text: `${nm(a)} et ${nm(b)}${fault}`, car: a ?? null, other: b ?? null, weight: 7 });
        break;
      }
      case "out":
        out.push({ t: e.t, kind: "out", title: "ABANDON", text: `${nm(e.car)} — ${REASON[e.reason ?? ""] ?? e.reason ?? "hors course"}`, car: e.car ?? null, weight: 8 });
        break;
      case "dsq":
        out.push({ t: e.t, kind: "dsq", title: "DISQUALIFICATION", text: `${nm(e.car)} — ${REASON[e.reason ?? ""] ?? "décision des commissaires"}`, car: e.car ?? null, weight: 8 });
        break;
      case "penalty":
        out.push({ t: e.t, kind: "penalty", title: "PÉNALITÉ", text: `${nm(e.car)} +${e.seconds ?? 5} s`, car: e.car ?? null, weight: 3 });
        break;
      case "finish":
        if (winner == null && race) {
          winner = e.car ?? null;
          out.push({ t: e.t, kind: "winner", title: "DRAPEAU À DAMIER", text: `${nm(e.car)} remporte la course !`, car: e.car ?? null, weight: 9 });
        }
        break;
    }
  }
  out.sort((a, b) => a.t - b.t);
  // les contacts suivis d'une pénalité au même instant : on garde le contact
  return out.filter((h, i) => !(h.kind === "penalty" && out.some((o, j) => j !== i && o.kind === "contact" && Math.abs(o.t - h.t) < 0.1)));
}

const COMPOUND_FR: Record<string, string> = { soft: "tendres", medium: "mediums", hard: "durs", wet: "pluie" };

// ------------------------------------------------------------------ caméra réalisateur
export interface Director {
  target: number | null;
  other: number | null;
  until: number; // temps réel (ms) jusqu'auquel on garde le plan
  untilT: number; // temps de course jusqu'auquel on garde le plan (lecture accélérée)
  /** décalage lissé du cadre par rapport à la voiture filmée, et zoom lissé */
  off: { x: number; y: number } | null;
  scale: number | null;
  shot: number | null; // voiture du plan en cours (changement = coupe franche)
  lastT: number;
}

export function newDirector(): Director {
  return { target: null, other: null, until: 0, untilT: Infinity, off: null, scale: null, shot: null, lastT: -Infinity };
}

/** Choisit la bagarre la plus serrée en tête de course (écart en distance), sinon le leader. */
function pickBattle(m: ReplayModel, t: number): [number | null, number | null] {
  const k = m.indexAt(t);
  if (k < 0) return [null, null];
  const F = m.F;
  const fr = m.frames[k];
  const order: number[] = [];
  for (let c = 0; c < m.nCars; c++) if (fr[c] && fr[c][F.status] === STATUS.racing) order.push(c);
  order.sort((a, b) => fr[a][F.pos] - fr[b][F.pos]);
  if (!order.length) return [null, null];
  let best: [number, number] | null = null;
  let bestScore = Infinity;
  for (let i = 0; i + 1 < order.length; i++) {
    const a = order[i], b = order[i + 1];
    const gap = Math.abs(m.dist[a][k] - m.dist[b][k]);
    if (gap > 60) continue;
    const score = gap + 6 * i; // préférer l'avant de la course
    if (score < bestScore) {
      bestScore = score;
      best = [a, b];
    }
  }
  return best ?? [order[0], null];
}

/**
 * Caméra « réalisateur ». Le cadre est attaché à la voiture filmée (aucun retard, même en lecture
 * accélérée) ; seuls le décalage (milieu d'une bagarre, regard devant) et le zoom sont lissés.
 * Un changement de voiture est une coupe franche, comme à la télévision.
 */
export function directorCamera(d: Director, m: ReplayModel, t: number, now: number, w: number, h: number,
  highlights: Highlight[], pinned: number | null, dtReal: number): Camera | null {
  if (t < d.lastT - 1 || t > d.lastT + 30) d.until = 0; // saut dans le temps
  for (const hl of highlights) {
    if (hl.t > d.lastT && hl.t <= t && hl.car != null && hl.weight >= 4) {
      d.target = hl.car;
      d.other = hl.other ?? null;
      d.until = now + (hl.weight >= 7 ? 5000 : 4000);
      d.untilT = hl.t + (hl.weight >= 7 ? 7 : 5);
    }
  }
  d.lastT = t;
  if (pinned != null) {
    d.target = pinned;
    d.other = null;
  } else if (now > d.until || t > d.untilT || d.target == null) {
    const [a, b] = pickBattle(m, t);
    d.target = a;
    d.other = b;
    d.until = now + 7000;
    d.untilT = t + 45;
  }
  if (d.target == null) return null;
  const pa = m.pose(d.target, t);
  if (!pa || pa.status === STATUS.pit) {
    d.until = 0;
    return null;
  }
  let ox = 0, oy = 0, span = 48;
  const pb = d.other != null ? m.pose(d.other, t) : null;
  if (pb && pb.status !== STATUS.pit && Math.hypot(pb.x - pa.x, pb.y - pa.y) < 160) {
    ox = (pb.x - pa.x) / 2;
    oy = (pb.y - pa.y) / 2;
    span = Math.max(48, Math.hypot(pb.x - pa.x, pb.y - pa.y) + 40);
  }
  ox += Math.cos(pa.psi) * 12;
  oy += Math.sin(pa.psi) * 12;
  const scale = Math.max(1.4, Math.min(9, Math.min(w, h) / span));
  if (d.shot !== d.target || !d.off || d.scale == null) {
    d.shot = d.target;
    d.off = { x: ox, y: oy };
    d.scale = scale;
  } else {
    const a = 1 - Math.exp(-dtReal * 2.5);
    d.off = { x: d.off.x + (ox - d.off.x) * a, y: d.off.y + (oy - d.off.y) * a };
    d.scale = d.scale * Math.pow(scale / d.scale, 1 - Math.exp(-dtReal * 1.5));
  }
  return { cx: pa.x + d.off.x, cy: pa.y + d.off.y, scale: d.scale };
}

// ------------------------------------------------------------------ rendu
export interface BroadcastOptions {
  ctx: CanvasRenderingContext2D;
  w: number;
  h: number;
  dpr: number;
  cam: Camera;
  model: ReplayModel;
  scene: Scenery;
  t: number;
  now: number; // temps réel (ms), pour les animations
  selected: number | null;
  focus: number | null;
  shortNames: string[];
  colors: string[];
  teammateIndex: number[];
}

function setWorld(o: BroadcastOptions) {
  const s = o.cam.scale * o.dpr;
  o.ctx.setTransform(s, 0, 0, -s, o.dpr * (o.w / 2 - o.cam.scale * o.cam.cx), o.dpr * (o.h / 2 + o.cam.scale * o.cam.cy));
}
function setScreen(o: BroadcastOptions) {
  o.ctx.setTransform(o.dpr, 0, 0, o.dpr, 0, 0);
}
function toScreen(o: BroadcastOptions, x: number, y: number): [number, number] {
  return [o.w / 2 + (x - o.cam.cx) * o.cam.scale, o.h / 2 - (y - o.cam.cy) * o.cam.scale];
}

export function drawBroadcast(o: BroadcastOptions): { x: number; y: number; car: number }[] {
  const { ctx, w, h, cam, model: m, scene: sc, t } = o;
  const g = m.geom;
  const px = 1 / cam.scale;

  // ---- fond : nuit urbaine
  setScreen(o);
  const bg = ctx.createLinearGradient(0, 0, 0, h);
  bg.addColorStop(0, C.bgTop);
  bg.addColorStop(1, C.bgBottom);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);

  setWorld(o);
  for (const b of sc.blocks) {
    ctx.fillStyle = b.lit ? C.blockLit : C.block;
    ctx.fillRect(b.x, b.y, b.w, b.h);
  }

  // asphalte, éclairé par les projecteurs (halo doux autour de la piste)
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.strokeStyle = "rgba(110,150,220,0.06)";
  ctx.lineWidth = 2 * sc.maxHw + 22;
  ctx.stroke(sc.centre);
  ctx.strokeStyle = "rgba(110,150,220,0.08)";
  ctx.lineWidth = 2 * sc.maxHw + 9;
  ctx.stroke(sc.centre);
  ctx.fillStyle = C.asphalt;
  ctx.fill(sc.asphalt, "evenodd");
  // trajectoire gommée au centre
  ctx.strokeStyle = C.racingLine;
  ctx.lineWidth = 4.5;
  ctx.stroke(sc.centre);

  // humidité : reflets bleutés
  const wet = sc.cells ? m.wetnessAt(t) : null;
  if (wet && sc.cells) {
    // couleur opaque mélangée (asphalte -> asphalte mouillé) : pas de coutures entre cellules
    for (let j = 0; j < sc.cells.length; j++) {
      const v = Math.min(1, wet.cells[j] ?? 0);
      if (v < 0.03) continue;
      const f = Math.round(v * 20) / 20; // paliers doux, limite les changements de style
      ctx.fillStyle = `rgb(${Math.round(58 + (40 - 58) * f)},${Math.round(64 + (82 - 64) * f)},${Math.round(75 + (128 - 75) * f)})`;
      ctx.fill(sc.cells[j]);
    }
  }

  // vibreurs
  if (cam.scale > 0.9) {
    ctx.fillStyle = C.kerbRed;
    ctx.fill(sc.kerbRed);
    ctx.fillStyle = C.kerbWhite;
    ctx.fill(sc.kerbWhite);
  }

  drawPitLane(o, px);

  // murs éclairés
  ctx.strokeStyle = "rgba(160,200,255,0.18)";
  ctx.lineWidth = Math.max(6 * px, 1.6);
  ctx.stroke(sc.left);
  ctx.stroke(sc.right);
  ctx.strokeStyle = C.wallGlow;
  ctx.lineWidth = Math.max(2.4 * px, 0.8);
  ctx.stroke(sc.left);
  ctx.stroke(sc.right);
  ctx.strokeStyle = C.wall;
  ctx.lineWidth = Math.max(0.9 * px, 0.45);
  ctx.stroke(sc.left);
  ctx.stroke(sc.right);

  // lampadaires
  if (cam.scale > 1.2) {
    const vw = o.w / cam.scale / 2 + 20, vh = o.h / cam.scale / 2 + 20;
    for (const l of sc.lamps) {
      if (Math.abs(l.x - cam.cx) > vw || Math.abs(l.y - cam.cy) > vh) continue;
      const gr = ctx.createRadialGradient(l.x, l.y, 0, l.x, l.y, 16);
      gr.addColorStop(0, "rgba(255,226,170,0.22)");
      gr.addColorStop(1, "rgba(255,226,170,0)");
      ctx.fillStyle = gr;
      ctx.fillRect(l.x - 16, l.y - 16, 32, 32);
    }
  }

  drawStartLine(o);

  // ---- voitures
  const k = m.indexAt(t);
  const F = m.F;
  const minLenPx = 16;
  const carScale = Math.max(1, minLenPx / (5 * cam.scale));
  const order: number[] = [];
  for (let c = 0; c < m.nCars; c++) if (c !== o.selected && c !== o.focus) order.push(c);
  order.sort((a, b) => (m.field(b, Math.max(0, k), "status") >= 3 ? 1 : 0) - (m.field(a, Math.max(0, k), "status") >= 3 ? 1 : 0));
  if (o.focus != null && o.focus !== o.selected) order.push(o.focus);
  if (o.selected != null) order.push(o.selected);
  const visible: { x: number; y: number; car: number }[] = [];
  const rain = wet?.rain ?? 0;

  for (const c of order) {
    const p = m.pose(c, t);
    if (!p || p.status === STATUS.pit) continue;
    let alpha = 1;
    const out = p.status === STATUS.dnf || p.status === STATUS.dsq;
    if (p.status === STATUS.finished) {
      const ft = m.finishT[c] ?? t;
      alpha = 1 - (t - ft) / 3;
      if (alpha <= 0) continue;
    } else if (out) {
      alpha = 0.55;
    }
    const speed = k >= 0 ? m.field(c, k, "speed") : 0;
    const pedal = k >= 0 && F.pedal != null ? m.field(c, k, "pedal") : 0;
    // traînée lumineuse
    if (!out && speed > 15) drawTrail(o, c, t, o.colors[c], alpha, carScale);
    // gerbe d'eau
    if (!out && rain > 0.05 && speed > 20) {
      const cell = wet ? wet.cells[Math.floor(((indexS(g, p.x, p.y, p.psi) % g.length) + g.length) % g.length / 10)] ?? 0 : 0;
      if (cell > 0.15) drawSpray(o, p.x, p.y, p.psi, Math.min(1, cell) * Math.min(1, speed / 60), carScale);
    }
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.translate(p.x, p.y);
    ctx.rotate(p.psi);
    ctx.scale(carScale, carScale);
    if (c === o.selected || c === o.focus) {
      ctx.shadowColor = o.colors[c];
      ctx.shadowBlur = 18;
    }
    drawCar(ctx, out ? C.grey : o.colors[c], o.teammateIndex[c] === 2, !out && pedal < -0.25, (m.field(c, Math.max(0, k), "pos") ?? 0) === 0 && !out);
    ctx.restore();
    if (!out && alpha > 0.5) visible.push({ x: p.x, y: p.y, car: c });
  }

  // étincelles des contacts récents
  for (const e of m.events) {
    if (e.type !== "contact" || e.t > t || t - e.t > 0.9) continue;
    const a = e.cars?.[0];
    const b = e.cars?.[1];
    const pa = a != null ? m.pose(a, e.t) : null;
    const pb = b != null ? m.pose(b, e.t) : null;
    if (!pa) continue;
    const x = pb ? (pa.x + pb.x) / 2 : pa.x;
    const y = pb ? (pa.y + pb.y) / 2 : pa.y;
    drawSparks(o, x, y, (t - e.t) / 0.9, Math.floor(e.t * 1000), carScale);
  }
  // fumée des abandons (quelques secondes)
  for (const e of m.events) {
    if ((e.type !== "out") || e.t > t || t - e.t > 6 || e.car == null) continue;
    const p = m.pose(e.car, t);
    if (p) drawSmoke(o, p.x, p.y, (t - e.t) / 6, carScale);
  }

  // ---- étiquettes façon télé
  setScreen(o);
  ctx.textBaseline = "middle";
  const showAll = cam.scale > 1.8;
  for (const v of visible) {
    const isSel = v.car === o.selected || v.car === o.focus;
    if (!showAll && !isSel) continue;
    const [sx, sy] = toScreen(o, v.x, v.y);
    if (sx < -60 || sy < -60 || sx > w + 60 || sy > h + 60) continue;
    const pos = (m.field(v.car, Math.max(0, k), "pos") ?? 0) + 1;
    drawTag(ctx, sx + 12, sy - 18, String(pos), o.shortNames[v.car], o.colors[v.car], isSel);
  }

  // ---- pluie à l'écran
  if (rain > 0.03) drawRain(o, rain);
  // vignettage
  const vg = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.35, w / 2, h / 2, Math.max(w, h) * 0.75);
  vg.addColorStop(0, "rgba(0,0,0,0)");
  vg.addColorStop(1, "rgba(0,0,0,0.55)");
  ctx.fillStyle = vg;
  ctx.fillRect(0, 0, w, h);
  return visible;
}

/** Abscisse approximative d'une position (pour l'humidité sous la voiture). */
function indexS(g: TrackGeom, x: number, y: number, _psi: number): number {
  let best = 0, bd = Infinity;
  for (let i = 0; i < g.n; i += 3) {
    const d = (g.cx[i] - x) ** 2 + (g.cy[i] - y) ** 2;
    if (d < bd) {
      bd = d;
      best = i;
    }
  }
  return g.s[best];
}

/** Monoplace vue de dessus, longueur 5 m, largeur 2 m, nez vers +x. */
function drawCar(ctx: CanvasRenderingContext2D, color: string, second: boolean, braking: boolean, leader: boolean) {
  // pneus
  ctx.fillStyle = "#0a0a0c";
  ctx.fillRect(1.25, 0.62, 0.62, 0.4);
  ctx.fillRect(1.25, -1.02, 0.62, 0.4);
  ctx.fillRect(-2.05, 0.6, 0.75, 0.45);
  ctx.fillRect(-2.05, -1.05, 0.75, 0.45);
  // aileron arrière
  ctx.fillStyle = color;
  ctx.fillRect(-2.5, -0.9, 0.38, 1.8);
  // coque
  ctx.beginPath();
  ctx.moveTo(2.5, 0);
  ctx.lineTo(1.6, 0.22);
  ctx.lineTo(0.6, 0.32);
  ctx.lineTo(0.1, 0.62);
  ctx.lineTo(-1.2, 0.6);
  ctx.lineTo(-2.1, 0.3);
  ctx.lineTo(-2.1, -0.3);
  ctx.lineTo(-1.2, -0.6);
  ctx.lineTo(0.1, -0.62);
  ctx.lineTo(0.6, -0.32);
  ctx.lineTo(1.6, -0.22);
  ctx.closePath();
  ctx.fill();
  ctx.shadowBlur = 0;
  // aileron avant
  ctx.fillRect(1.95, -0.95, 0.32, 1.9);
  // livrée de la voiture n° 2 : bande blanche
  if (second) {
    ctx.fillStyle = "rgba(255,255,255,0.92)";
    ctx.fillRect(-1.9, -0.12, 3.9, 0.24);
  }
  // cockpit + casque
  ctx.fillStyle = "#07080b";
  ctx.beginPath();
  ctx.ellipse(-0.25, 0, 0.55, 0.26, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = leader ? "#ffd84a" : "#e9edf3";
  ctx.beginPath();
  ctx.arc(-0.2, 0, 0.17, 0, Math.PI * 2);
  ctx.fill();
  // feux stop
  if (braking) {
    ctx.fillStyle = "#ff2a2a";
    ctx.shadowColor = "#ff2a2a";
    ctx.shadowBlur = 14;
    ctx.fillRect(-2.62, -0.14, 0.16, 0.28);
    ctx.shadowBlur = 0;
  }
}

function drawTrail(o: BroadcastOptions, c: number, t: number, color: string, alpha: number, carScale: number) {
  const { ctx, model: m } = o;
  const head = m.pose(c, t);
  if (!head) return;
  const back = 2.3 * carScale;
  const pts: [number, number][] = [[head.x - Math.cos(head.psi) * back, head.y - Math.sin(head.psi) * back]];
  for (let i = 1; i <= 6; i++) {
    const p = m.pose(c, t - 0.045 * i);
    if (!p || p.status !== STATUS.racing) break;
    const q: [number, number] = [p.x - Math.cos(p.psi) * back, p.y - Math.sin(p.psi) * back];
    const last = pts[pts.length - 1];
    if (Math.hypot(q[0] - last[0], q[1] - last[1]) > 30) break;
    pts.push(q);
  }
  if (pts.length < 2) return;
  const [hx, hy] = pts[0];
  const [tx, ty] = pts[pts.length - 1];
  const gr = ctx.createLinearGradient(hx, hy, tx, ty);
  gr.addColorStop(0, color);
  gr.addColorStop(1, "rgba(0,0,0,0)");
  ctx.save();
  ctx.globalAlpha = alpha * 0.7;
  ctx.globalCompositeOperation = "lighter";
  ctx.strokeStyle = gr;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.lineWidth = 1.1 * carScale;
  ctx.beginPath();
  ctx.moveTo(hx, hy);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  ctx.stroke();
  ctx.restore();
}

function drawSpray(o: BroadcastOptions, x: number, y: number, psi: number, k: number, carScale: number) {
  const { ctx } = o;
  const bx = x - Math.cos(psi) * 5 * carScale;
  const by = y - Math.sin(psi) * 5 * carScale;
  const r = 6 * carScale * (0.6 + k);
  const gr = ctx.createRadialGradient(bx, by, 0, bx, by, r);
  gr.addColorStop(0, `rgba(200,215,235,${0.38 * k})`);
  gr.addColorStop(1, "rgba(200,215,235,0)");
  ctx.fillStyle = gr;
  ctx.beginPath();
  ctx.arc(bx, by, r, 0, Math.PI * 2);
  ctx.fill();
}

function drawSparks(o: BroadcastOptions, x: number, y: number, f: number, seed: number, carScale: number) {
  const { ctx } = o;
  const r = rng(seed + 1);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (let i = 0; i < 26; i++) {
    const a = r() * Math.PI * 2;
    const v = (4 + r() * 12) * carScale;
    const d0 = v * f;
    const d1 = d0 + 1.6 * carScale;
    ctx.strokeStyle = i % 3 === 0 ? `rgba(255,255,220,${1 - f})` : `rgba(255,${150 + Math.floor(r() * 80)},40,${1 - f})`;
    ctx.lineWidth = 0.35 * carScale;
    ctx.beginPath();
    ctx.moveTo(x + Math.cos(a) * d0, y + Math.sin(a) * d0);
    ctx.lineTo(x + Math.cos(a) * d1, y + Math.sin(a) * d1);
    ctx.stroke();
  }
  const gr = ctx.createRadialGradient(x, y, 0, x, y, 7 * carScale);
  gr.addColorStop(0, `rgba(255,220,140,${0.7 * (1 - f)})`);
  gr.addColorStop(1, "rgba(255,160,60,0)");
  ctx.fillStyle = gr;
  ctx.beginPath();
  ctx.arc(x, y, 7 * carScale, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawSmoke(o: BroadcastOptions, x: number, y: number, f: number, carScale: number) {
  const { ctx } = o;
  for (let i = 0; i < 4; i++) {
    const r = (3 + i * 2.5 + f * 10) * carScale;
    const gr = ctx.createRadialGradient(x + i, y + i * 1.5, 0, x + i, y + i * 1.5, r);
    gr.addColorStop(0, `rgba(150,150,160,${0.28 * (1 - f)})`);
    gr.addColorStop(1, "rgba(150,150,160,0)");
    ctx.fillStyle = gr;
    ctx.beginPath();
    ctx.arc(x + i, y + i * 1.5, r, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawRain(o: BroadcastOptions, rain: number) {
  const { ctx, w, h, now } = o;
  const n = Math.floor(90 + rain * 260);
  const r = rng(12345);
  ctx.strokeStyle = `rgba(190,210,240,${0.18 + rain * 0.22})`;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 0; i < n; i++) {
    const sp = 0.6 + r() * 0.8;
    const x0 = r() * (w + 100);
    const y0 = r() * h;
    const y = (y0 + now * sp) % (h + 40) - 20;
    const x = (x0 - now * sp * 0.25) % (w + 100);
    const xx = x < 0 ? x + w + 100 : x;
    ctx.moveTo(xx, y);
    ctx.lineTo(xx - 4, y + 14);
  }
  ctx.stroke();
}

function drawTag(ctx: CanvasRenderingContext2D, x: number, y: number, pos: string, name: string, color: string, hi: boolean) {
  ctx.font = "800 11px var(--font-sans, system-ui), system-ui, sans-serif";
  const pw = Math.max(16, ctx.measureText(pos).width + 8);
  const nw = ctx.measureText(name).width + 12;
  const hgt = 18;
  ctx.fillStyle = hi ? "#ffffff" : "rgba(255,255,255,0.92)";
  ctx.fillRect(x, y - hgt / 2, pw, hgt);
  ctx.fillStyle = "#05070c";
  ctx.textAlign = "center";
  ctx.fillText(pos, x + pw / 2, y + 0.5);
  ctx.fillStyle = C.labelBg;
  ctx.fillRect(x + pw, y - hgt / 2, nw, hgt);
  ctx.fillStyle = color;
  ctx.fillRect(x + pw, y - hgt / 2, 3, hgt);
  ctx.fillStyle = C.label;
  ctx.textAlign = "left";
  ctx.fillText(name, x + pw + 7, y + 0.5);
}

function drawStartLine(o: BroadcastOptions) {
  const { ctx, model } = o;
  const g = model.geom;
  const hw = g.hw[0];
  const cx = g.cx[0], cy = g.cy[0], nx = g.nx[0], ny = g.ny[0], tx = g.tx[0], ty = g.ty[0];
  const sq = 1;
  const n = Math.floor((2 * hw) / sq);
  for (let row = 0; row < 2; row++) {
    for (let k = 0; k < n; k++) {
      ctx.fillStyle = (k + row) % 2 === 0 ? "#0a0a0a" : "#f6f6f6";
      const lat = -hw + k * sq;
      const lon = (row - 1) * sq;
      const x0 = cx + nx * lat + tx * lon, y0 = cy + ny * lat + ty * lon;
      ctx.beginPath();
      ctx.moveTo(x0, y0);
      ctx.lineTo(x0 + nx * sq, y0 + ny * sq);
      ctx.lineTo(x0 + nx * sq + tx * sq, y0 + ny * sq + ty * sq);
      ctx.lineTo(x0 + tx * sq, y0 + ty * sq);
      ctx.closePath();
      ctx.fill();
    }
  }
}

function drawPitLane(o: BroadcastOptions, px: number) {
  const { ctx, model } = o;
  const g = model.geom;
  const L = g.length;
  const s0 = model.header.track.pit_entry_s;
  let s1 = model.header.track.pit_exit_s;
  if (s1 <= s0) s1 += L;
  ctx.beginPath();
  let first = true;
  for (let s = s0; s <= s1; s += 4) {
    const i = indexAtS(g, s);
    const off = g.hw[i] + 5;
    const x = g.cx[i] - g.nx[i] * off, y = g.cy[i] - g.ny[i] * off;
    if (first) {
      ctx.moveTo(g.cx[i] - g.nx[i] * g.hw[i], g.cy[i] - g.ny[i] * g.hw[i]);
      first = false;
    }
    ctx.lineTo(x, y);
  }
  const ie = indexAtS(g, s1);
  ctx.lineTo(g.cx[ie] - g.nx[ie] * g.hw[ie], g.cy[ie] - g.ny[ie] * g.hw[ie]);
  ctx.strokeStyle = "#1b212b";
  ctx.lineWidth = 5;
  ctx.stroke();
  ctx.strokeStyle = C.pit;
  ctx.globalAlpha = 0.8;
  ctx.lineWidth = Math.max(0.5, 1.2 * px);
  ctx.setLineDash([3, 3]);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
}
