// Rendu canvas 2D du circuit et des voitures. Coordonnées monde en mètres (y vers le haut).
import { STATUS, indexAtS, type ReplayModel, type TrackGeom } from "@/lib/replay-model";

export interface Camera {
  cx: number;
  cy: number;
  scale: number; // pixels CSS par mètre
}

export interface Theme {
  bg: string;
  grid: string;
  asphalt: string;
  edge: string;
  kerb: string;
  line: string;
  pit: string;
  wet: string;
  accent: string;
  accentText: string;
  label: string;
  labelBg: string;
  outline: string;
  grey: string;
  font: string;
}

export const DEFAULT_THEME: Theme = {
  bg: "#0d1117",
  grid: "rgba(255,255,255,0.035)",
  asphalt: "#2a3039",
  edge: "#e9edf2",
  kerb: "#d64545",
  line: "#ffffff",
  pit: "#f5b740",
  wet: "#3f8cff",
  accent: "#c8f04a",
  accentText: "#c8f04a",
  label: "#e8ecf1",
  labelBg: "rgba(13,17,23,0.82)",
  outline: "#0b0e13",
  grey: "#7d8796",
  font: "system-ui, sans-serif",
};

export function readTheme(el: Element): Theme {
  const cs = getComputedStyle(el);
  const v = (name: string, fb: string) => cs.getPropertyValue(name).trim() || fb;
  return {
    bg: v("--canvas-bg", DEFAULT_THEME.bg),
    grid: v("--canvas-grid", DEFAULT_THEME.grid),
    asphalt: v("--asphalt", DEFAULT_THEME.asphalt),
    edge: v("--track-edge", DEFAULT_THEME.edge),
    kerb: v("--track-kerb", DEFAULT_THEME.kerb),
    line: v("--track-line", DEFAULT_THEME.line),
    pit: v("--pit", DEFAULT_THEME.pit),
    wet: v("--wet", DEFAULT_THEME.wet),
    accent: v("--accent", DEFAULT_THEME.accent),
    accentText: v("--accent-text", DEFAULT_THEME.accentText),
    label: v("--canvas-label", DEFAULT_THEME.label),
    labelBg: v("--canvas-label-bg", DEFAULT_THEME.labelBg),
    outline: v("--canvas-outline", DEFAULT_THEME.outline),
    grey: v("--canvas-grey", DEFAULT_THEME.grey),
    font: getComputedStyle(document.body).fontFamily || DEFAULT_THEME.font,
  };
}

// ------------------------------------------------------------------ chemins pré-calculés
export interface TrackPaths {
  asphalt: Path2D;
  left: Path2D;
  right: Path2D;
  cells: Path2D[] | null;
}

export function buildTrackPaths(g: TrackGeom, withCells: boolean, cellSize = 10): TrackPaths {
  const asphalt = new Path2D();
  const left = new Path2D();
  const right = new Path2D();
  for (let i = 0; i < g.n; i++) {
    const lx = g.cx[i] + g.nx[i] * g.hw[i];
    const ly = g.cy[i] + g.ny[i] * g.hw[i];
    if (i === 0) left.moveTo(lx, ly);
    else left.lineTo(lx, ly);
  }
  left.closePath();
  for (let i = 0; i < g.n; i++) {
    const rx = g.cx[i] - g.nx[i] * g.hw[i];
    const ry = g.cy[i] - g.ny[i] * g.hw[i];
    if (i === 0) right.moveTo(rx, ry);
    else right.lineTo(rx, ry);
  }
  right.closePath();
  // anneau = bord gauche + bord droit, rempli en pair-impair
  asphalt.addPath(left);
  asphalt.addPath(right);

  let cells: Path2D[] | null = null;
  if (withCells) {
    cells = [];
    const nCells = Math.ceil(g.length / cellSize);
    for (let j = 0; j < nCells; j++) {
      const a = indexAtS(g, j * cellSize);
      let b = indexAtS(g, Math.min(g.length - 0.01, (j + 1) * cellSize));
      if (b < a) b = g.n - 1;
      const p = new Path2D();
      for (let i = a; i <= Math.min(b + 1, g.n - 1); i++) {
        const x = g.cx[i] + g.nx[i] * g.hw[i];
        const y = g.cy[i] + g.ny[i] * g.hw[i];
        if (i === a) p.moveTo(x, y);
        else p.lineTo(x, y);
      }
      for (let i = Math.min(b + 1, g.n - 1); i >= a; i--) p.lineTo(g.cx[i] - g.nx[i] * g.hw[i], g.cy[i] - g.ny[i] * g.hw[i]);
      p.closePath();
      cells.push(p);
    }
  }
  return { asphalt, left, right, cells };
}

/** Cadre tout le circuit ; `top` réserve une bande en haut (indicateurs superposés). */
export function fitCamera(g: TrackGeom, w: number, h: number, pad = 24, top = 0): Camera {
  const bw = g.bbox.maxX - g.bbox.minX;
  const bh = g.bbox.maxY - g.bbox.minY;
  const scale = Math.max(0.05, Math.min((w - 2 * pad) / bw, (h - 2 * pad - top) / bh));
  // centre du contenu décalé vers le bas de top/2 pixels
  return { cx: (g.bbox.minX + g.bbox.maxX) / 2, cy: (g.bbox.minY + g.bbox.maxY) / 2 + top / 2 / scale, scale };
}

export function worldToScreen(cam: Camera, w: number, h: number, x: number, y: number): [number, number] {
  return [w / 2 + (x - cam.cx) * cam.scale, h / 2 - (y - cam.cy) * cam.scale];
}

export function screenToWorld(cam: Camera, w: number, h: number, sx: number, sy: number): [number, number] {
  return [cam.cx + (sx - w / 2) / cam.scale, cam.cy - (sy - h / 2) / cam.scale];
}

// ------------------------------------------------------------------ rendu
export interface DrawOptions {
  ctx: CanvasRenderingContext2D;
  w: number;
  h: number;
  dpr: number;
  cam: Camera;
  model: ReplayModel;
  paths: TrackPaths;
  theme: Theme;
  t: number;
  selected: number | null;
  shortNames: string[];
  colors: string[];
  teammateIndex: number[]; // 1 ou 2
}

function setWorld(o: DrawOptions) {
  const { ctx, dpr, cam, w, h } = o;
  const s = cam.scale * dpr;
  ctx.setTransform(s, 0, 0, -s, dpr * (w / 2 - cam.scale * cam.cx), dpr * (h / 2 + cam.scale * cam.cy));
}

function setScreen(o: DrawOptions) {
  o.ctx.setTransform(o.dpr, 0, 0, o.dpr, 0, 0);
}

export function drawScene(o: DrawOptions): { x: number; y: number; car: number }[] {
  const { ctx, w, h, cam, model, paths, theme: th, t } = o;
  const g = model.geom;
  const px = 1 / cam.scale; // 1 pixel CSS en mètres

  setScreen(o);
  ctx.fillStyle = th.bg;
  ctx.fillRect(0, 0, w, h);

  // ---- circuit
  setWorld(o);
  ctx.fillStyle = th.asphalt;
  ctx.fill(paths.asphalt, "evenodd");

  // humidité (teinte bleue par cellule de 10 m, interpolée dans le temps)
  const wet = paths.cells ? model.wetnessAt(t) : null;
  if (wet && paths.cells) {
    ctx.fillStyle = th.wet;
    for (let j = 0; j < paths.cells.length; j++) {
      const v = wet.cells[j] ?? 0;
      if (v < 0.02) continue;
      ctx.globalAlpha = Math.min(0.6, v * 0.6);
      ctx.fill(paths.cells[j]);
    }
    ctx.globalAlpha = 1;
  }

  // voie des stands (pointillés, côté droit) entre l'entrée et la sortie
  drawPitLane(o, px);

  // bords = murs : trait plein, façon vibreur quand on zoome
  ctx.lineJoin = "round";
  ctx.strokeStyle = th.edge;
  ctx.lineWidth = Math.max(0.9, 1.4 * px);
  ctx.stroke(paths.left);
  ctx.stroke(paths.right);
  if (cam.scale > 1.6) {
    ctx.strokeStyle = th.kerb;
    ctx.setLineDash([3, 3]);
    ctx.stroke(paths.left);
    ctx.stroke(paths.right);
    ctx.setLineDash([]);
  }

  drawStartLine(o, px);
  drawPitMarker(o, model.header.track.pit_entry_s, px);
  drawPitMarker(o, model.header.track.pit_exit_s, px);

  // ---- voitures
  const carScale = Math.max(1, 11 / (5 * cam.scale)); // au moins 11 px de long
  const Lc = 5 * carScale;
  const Wc = 2 * carScale;
  const order: number[] = [];
  for (let c = 0; c < model.nCars; c++) if (c !== o.selected) order.push(c);
  // éliminés dessous, sélection au-dessus
  order.sort((a, b) => {
    const sa = model.field(a, Math.max(0, model.indexAt(t)), "status");
    const sb = model.field(b, Math.max(0, model.indexAt(t)), "status");
    return (sb >= 3 ? 1 : 0) - (sa >= 3 ? 1 : 0);
  });
  if (o.selected != null) order.push(o.selected);
  const visible: { x: number; y: number; car: number }[] = [];

  for (const c of order) {
    const p = model.pose(c, t);
    if (!p) continue;
    let alpha = 1;
    let fill = o.colors[c];
    if (p.status === STATUS.pit) continue; // retirée de la piste
    if (p.status === STATUS.finished) {
      const ft = model.finishT[c] ?? t;
      alpha = 1 - (t - ft) / 2.5;
      if (alpha <= 0) continue;
    } else if (p.status === STATUS.dnf || p.status === STATUS.dsq) {
      alpha = 0.4;
      fill = th.grey;
    }
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.translate(p.x, p.y);
    ctx.rotate(p.psi);
    ctx.fillStyle = fill;
    ctx.fillRect(-Lc / 2, -Wc / 2, Lc, Wc);
    // nez légèrement plus sombre pour lire le sens
    ctx.fillStyle = "rgba(0,0,0,0.28)";
    ctx.fillRect(Lc / 2 - Lc * 0.22, -Wc / 2, Lc * 0.22, Wc);
    // voiture n° 2 de l'écurie : bande centrale claire
    if (o.teammateIndex[c] === 2) {
      ctx.fillStyle = "rgba(255,255,255,0.92)";
      ctx.fillRect(-Lc / 2, -Wc * 0.16, Lc * 0.78, Wc * 0.32);
    }
    ctx.lineWidth = Math.max(0.12, 1 * px);
    ctx.strokeStyle = th.outline;
    ctx.strokeRect(-Lc / 2, -Wc / 2, Lc, Wc);
    ctx.restore();
    if (p.status !== STATUS.dnf && p.status !== STATUS.dsq && alpha > 0.5) visible.push({ x: p.x, y: p.y, car: c });
  }

  // ---- étiquettes (espace écran)
  setScreen(o);
  ctx.font = `600 11px ${th.font}`;
  ctx.textBaseline = "middle";
  const showAll = cam.scale > 2.2;
  for (const v of visible) {
    const isSel = v.car === o.selected;
    if (!showAll && !isSel) continue;
    const [sx, sy] = worldToScreen(cam, w, h, v.x, v.y);
    if (sx < -40 || sy < -40 || sx > w + 40 || sy > h + 40) continue;
    if (isSel) {
      ctx.beginPath();
      ctx.arc(sx, sy, Math.max(13, Lc * cam.scale * 0.75), 0, Math.PI * 2);
      ctx.strokeStyle = th.accentText;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    const text = o.shortNames[v.car];
    const tw = ctx.measureText(text).width;
    const lx = sx + (isSel ? 18 : 10);
    const ly = sy - (isSel ? 16 : 10);
    ctx.fillStyle = th.labelBg;
    roundRect(ctx, lx - 4, ly - 8, tw + 8, 16, 3);
    ctx.fill();
    ctx.fillStyle = o.colors[v.car];
    ctx.fillRect(lx - 4, ly - 8, 2, 16);
    ctx.fillStyle = isSel ? th.accentText : th.label;
    ctx.fillText(text, lx + 1, ly + 0.5);
  }
  return visible;
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function drawStartLine(o: DrawOptions, px: number) {
  const { ctx, model, cam, theme } = o;
  const g = model.geom;
  const i = 0;
  const hw = g.hw[i];
  const cx = g.cx[i], cy = g.cy[i];
  const nx = g.nx[i], ny = g.ny[i];
  const tx = g.tx[i], ty = g.ty[i];
  if (cam.scale < 1.6) {
    ctx.strokeStyle = theme.line;
    ctx.lineWidth = Math.max(1.2, 2.5 * px);
    ctx.beginPath();
    ctx.moveTo(cx + nx * hw, cy + ny * hw);
    ctx.lineTo(cx - nx * hw, cy - ny * hw);
    ctx.stroke();
    return;
  }
  // damier : deux rangées de carrés de 1 m
  const sq = 1;
  const n = Math.floor((2 * hw) / sq);
  for (let row = 0; row < 2; row++) {
    for (let k = 0; k < n; k++) {
      const black = (k + row) % 2 === 0;
      ctx.fillStyle = black ? "#111" : "#f4f4f4";
      const lat = -hw + k * sq;
      const lon = (row - 1) * sq;
      const x0 = cx + nx * lat + tx * lon;
      const y0 = cy + ny * lat + ty * lon;
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

function pitOffset(o: DrawOptions, i: number, px: number) {
  return o.model.geom.hw[i] + Math.max(3.5, 7 * px);
}

function drawPitLane(o: DrawOptions, px: number) {
  const { ctx, model, theme } = o;
  const g = model.geom;
  const L = g.length;
  const s0 = model.header.track.pit_entry_s;
  let s1 = model.header.track.pit_exit_s;
  if (s1 <= s0) s1 += L;
  ctx.beginPath();
  const step = 4;
  let first = true;
  for (let s = s0; s <= s1; s += step) {
    const i = indexAtS(g, s);
    const off = pitOffset(o, i, px);
    const x = g.cx[i] - g.nx[i] * off;
    const y = g.cy[i] - g.ny[i] * off;
    if (first) {
      // raccord depuis le bord de piste
      ctx.moveTo(g.cx[i] - g.nx[i] * g.hw[i], g.cy[i] - g.ny[i] * g.hw[i]);
      first = false;
    }
    ctx.lineTo(x, y);
  }
  const ie = indexAtS(g, s1);
  ctx.lineTo(g.cx[ie] - g.nx[ie] * g.hw[ie], g.cy[ie] - g.ny[ie] * g.hw[ie]);
  ctx.strokeStyle = theme.pit;
  ctx.globalAlpha = 0.75;
  ctx.lineWidth = Math.max(0.8, 1.5 * px);
  ctx.setLineDash([Math.max(2, 4 * px), Math.max(2, 4 * px)]);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
}

function drawPitMarker(o: DrawOptions, s: number, px: number) {
  const { ctx, model, theme } = o;
  const g = model.geom;
  const i = indexAtS(g, s);
  const off = pitOffset(o, i, px);
  ctx.strokeStyle = theme.pit;
  ctx.lineWidth = Math.max(1, 3 * px);
  ctx.beginPath();
  ctx.moveTo(g.cx[i], g.cy[i]);
  ctx.lineTo(g.cx[i] - g.nx[i] * off, g.cy[i] - g.ny[i] * off);
  ctx.stroke();
}

/** Voiture la plus proche d'un point écran (rayon en px), pour la sélection au clic. */
export function pickCar(visible: { x: number; y: number; car: number }[], cam: Camera, w: number, h: number, sx: number, sy: number, radius = 18): number | null {
  let best: number | null = null;
  let bestD = radius * radius;
  for (const v of visible) {
    const [x, y] = worldToScreen(cam, w, h, v.x, v.y);
    const d = (x - sx) ** 2 + (y - sy) ** 2;
    if (d < bestD) {
      bestD = d;
      best = v.car;
    }
  }
  return best;
}
