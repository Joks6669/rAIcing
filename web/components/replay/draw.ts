// Caméra du replay : coordonnées monde en mètres (y vers le haut), conversions écran et sélection.
// Le rendu de la scène est dans broadcast.ts.
import type { TrackGeom } from "@/lib/replay-model";

export interface Camera {
  cx: number;
  cy: number;
  scale: number; // pixels CSS par mètre
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
