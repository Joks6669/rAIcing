"use client";
// Fond animé de la page d'accueil : la dernière course rejouée en boucle par la caméra « réalisateur »,
// sans habillage. Image fixe si l'utilisateur a demandé à réduire les animations.
import { useEffect, useRef, useState } from "react";
import { ReplayModel } from "@/lib/replay-model";
import { decodeReplay, replayUrl } from "./ReplayLoader";
import { buildHighlights, buildScenery, directorCamera, drawBroadcast, newDirector } from "./replay/broadcast";

const SPEED = 1.6;

export default function HeroReplay({ roundId }: { roundId: number }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let raf = 0;
    let ro: ResizeObserver | null = null;
    fetch(replayUrl(roundId, "race"))
      .then(async (res) => {
        if (!res.ok) throw new Error(String(res.status));
        const { frames, ...header } = await decodeReplay(res);
        if (cancelled || !frames?.length) return;
        const model = new ReplayModel(header, frames);
        const scene = buildScenery(model.geom, model.anyWet);
        const highlights = buildHighlights(model);
        const director = newDirector();
        const colors = model.header.cars.map((c) => c.color || "#888");
        const names = model.header.cars.map((c) => c.driver.slice(0, 3).toUpperCase());
        const mates = model.header.cars.map((c) => (c.entry_id?.endsWith("#2") ? 2 : 1));
        const canvas = canvasRef.current!;
        const wrap = wrapRef.current!;
        const size = { w: 800, h: 400, dpr: 1 };
        ro = new ResizeObserver(() => {
          const r = wrap.getBoundingClientRect();
          size.dpr = Math.min(2, window.devicePixelRatio || 1);
          size.w = r.width;
          size.h = r.height;
          canvas.width = Math.round(r.width * size.dpr);
          canvas.height = Math.round(r.height * size.dpr);
        });
        ro.observe(wrap);
        const span = model.tEnd - model.t0;
        const start = model.t0 + Math.min(25, span * 0.05);
        let t = start;
        let last = performance.now();
        const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        const frame = (now: number) => {
          const dt = Math.min(0.1, (now - last) / 1000);
          last = now;
          if (!still) {
            t += dt * SPEED;
            if (t > model.tEnd - 2) t = start;
          }
          if (size.w > 0 && size.h > 0) {
            const cam = directorCamera(director, model, t, now, size.w, size.h, highlights, null, dt);
            if (cam) {
              drawBroadcast({ ctx, w: size.w, h: size.h, dpr: size.dpr, cam, model, scene, t, now, selected: null, focus: director.target, shortNames: names, colors, teammateIndex: mates });
            }
          }
          if (!still) raf = requestAnimationFrame(frame);
        };
        setReady(true);
        raf = requestAnimationFrame(frame);
      })
      .catch(() => {
        /* fond statique (dégradé CSS) */
      });
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      ro?.disconnect();
    };
  }, [roundId]);

  return (
    <div className={`hero-replay ${ready ? "is-ready" : ""}`} ref={wrapRef} aria-hidden>
      <canvas ref={canvasRef} />
    </div>
  );
}
