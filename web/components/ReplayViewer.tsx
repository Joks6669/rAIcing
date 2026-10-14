"use client";
// Visionneuse de replay (canvas 2D) : circuit, voitures interpolées, caméra, classement en direct,
// télémétrie de la voiture sélectionnée, fil des événements. Fonctionne sur un replay complet
// ou sur un flux d'images ajoutées au fil de l'eau (page Direct).
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ReplayModel, STATUS } from "@/lib/replay-model";
import { fmtClock } from "@/lib/format";
import type { CompoundSpec } from "@/lib/types";
import {
  buildTrackPaths,
  drawScene,
  fitCamera,
  pickCar,
  readTheme,
  screenToWorld,
  DEFAULT_THEME,
  type Camera,
  type Theme,
  type TrackPaths,
} from "./replay/draw";
import Leaderboard from "./replay/Leaderboard";
import Telemetry from "./replay/Telemetry";
import EventsTicker from "./replay/EventsTicker";
import Timeline from "./replay/Timeline";

const SPEEDS = [1, 2, 5, 10, 30];
const LIVE_DELAY = 0.45; // s de retard sur la dernière image reçue, pour interpoler
type CamMode = "overview" | "follow" | "free";

export interface ReplayViewerProps {
  model: ReplayModel;
  live?: boolean;
  compounds?: Record<string, CompoundSpec>;
  /** voiture sélectionnée au départ (index), sinon le poleman */
  initialSelected?: number | null;
  initialSpeed?: number;
  autoplay?: boolean;
}

function shortName(name: string): string {
  return name.slice(0, 3).toUpperCase();
}

export default function ReplayViewer({ model, live = false, compounds, initialSelected, initialSpeed = 5, autoplay = false }: ReplayViewerProps) {
  const subscribe = useCallback((cb: () => void) => model.subscribe(cb), [model]);
  const version = useSyncExternalStore(subscribe, () => model.version, () => model.version);

  const [playing, setPlaying] = useState(autoplay || live);
  const [speed, setSpeed] = useState(initialSpeed);
  const [followLive, setFollowLive] = useState(live);
  const [camMode, setCamMode] = useState<CamMode>("overview");
  const [selected, setSelected] = useState<number | null>(() => {
    if (initialSelected != null) return initialSelected;
    if (!model.frames.length) return null;
    const f0 = model.frames[0];
    const idx = f0.findIndex((d) => d && d[model.F.pos] === 0);
    return idx >= 0 ? idx : 0;
  });
  const [uiT, setUiT] = useState(() => (live ? model.tEnd : model.t0));

  const tRef = useRef(live ? model.tEnd : model.t0);
  const stateRef = useRef({ playing, speed, followLive, camMode, selected });
  stateRef.current = { playing, speed, followLive, camMode, selected };

  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sizeRef = useRef({ w: 800, h: 520, dpr: 1 });
  const camRef = useRef<Camera>({ cx: 0, cy: 0, scale: 1 });
  const followScaleRef = useRef(3.2);
  const themeRef = useRef<Theme>(DEFAULT_THEME);
  const visibleRef = useRef<{ x: number; y: number; car: number }[]>([]);
  const pathsRef = useRef<TrackPaths | null>(null);

  const cars = model.header.cars;
  const colors = useMemo(() => cars.map((c) => c.color || "#888"), [cars]);
  const shortNames = useMemo(() => cars.map((c) => shortName(c.driver)), [cars]);
  const teammateIndex = useMemo(
    () => cars.map((c) => (c.entry_id?.endsWith("#2") ? 2 : c.entry_id?.endsWith("#1") ? 1 : cars.filter((o) => o.team_id === c.team_id).indexOf(c) + 1)),
    [cars],
  );

  // ------------------------------------------------------------ dessin
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !model.frames.length) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (!pathsRef.current) pathsRef.current = buildTrackPaths(model.geom, model.anyWet);
    const { w, h, dpr } = sizeRef.current;
    const st = stateRef.current;
    const t = tRef.current;
    let cam: Camera;
    if (st.camMode === "overview") {
      cam = fitCamera(model.geom, w, h, w < 500 ? 12 : 24, w < 500 ? 34 : 20);
    } else if (st.camMode === "follow" && st.selected != null) {
      const p = model.pose(st.selected, t);
      cam = p && p.status !== STATUS.pit ? { cx: p.x, cy: p.y, scale: followScaleRef.current } : { ...camRef.current, scale: followScaleRef.current };
    } else {
      cam = camRef.current;
    }
    camRef.current = cam;
    visibleRef.current = drawScene({
      ctx, w, h, dpr, cam, model, paths: pathsRef.current, theme: themeRef.current, t,
      selected: st.selected, shortNames, colors, teammateIndex,
    });
  }, [model, shortNames, colors, teammateIndex]);

  // taille HiDPI
  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    const ro = new ResizeObserver(() => {
      const r = wrap.getBoundingClientRect();
      const dpr = Math.min(3, window.devicePixelRatio || 1);
      sizeRef.current = { w: r.width, h: r.height, dpr };
      canvas.width = Math.round(r.width * dpr);
      canvas.height = Math.round(r.height * dpr);
      canvas.style.width = `${r.width}px`;
      canvas.style.height = `${r.height}px`;
      draw();
    });
    ro.observe(wrap);
    return () => ro.disconnect();
  }, [draw]);

  // thème (couleurs lues sur les variables CSS)
  useEffect(() => {
    const update = () => {
      if (wrapRef.current) themeRef.current = readTheme(wrapRef.current);
      draw();
    };
    update();
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, [draw]);

  // boucle d'animation
  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    let lastUi = 0;
    const loop = (now: number) => {
      const dtReal = Math.min(0.1, (now - last) / 1000);
      last = now;
      const st = stateRef.current;
      if (live && st.followLive) {
        const edge = model.tEnd - LIVE_DELAY;
        let t = tRef.current + dtReal;
        if (t > model.tEnd) t = model.tEnd;
        if (t < edge - 1.5 || t > edge + 0.6) t = Math.max(model.t0, edge);
        tRef.current = Math.max(model.t0, t);
      } else if (st.playing && model.frames.length) {
        tRef.current += dtReal * (live ? 1 : st.speed);
        if (live && tRef.current >= model.tEnd - LIVE_DELAY) {
          // rattrapage du direct : on repasse en suivi du flux
          tRef.current = Math.max(model.t0, model.tEnd - LIVE_DELAY);
          setFollowLive(true);
        } else if (tRef.current >= model.tEnd) {
          tRef.current = model.tEnd;
          if (!live) setPlaying(false);
        }
      }
      draw();
      if (now - lastUi > 110) {
        lastUi = now;
        setUiT(tRef.current);
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [model, live, draw]);

  // nouveau modèle (direct) : nouvelle géométrie
  useEffect(() => {
    pathsRef.current = null;
  }, [model]);

  // sélection par défaut quand les premières images arrivent en direct
  useEffect(() => {
    if (selected == null && model.frames.length) {
      const f0 = model.frames[model.frames.length - 1];
      const idx = f0.findIndex((d) => d && d[model.F.pos] === 0);
      setSelected(idx >= 0 ? idx : 0);
    }
  }, [version, model, selected]);

  // ------------------------------------------------------------ actions
  const seek = useCallback(
    (t: number) => {
      tRef.current = Math.max(model.t0, Math.min(model.tEnd, t));
      setUiT(tRef.current);
      if (live) setFollowLive(model.tEnd - tRef.current < 1);
    },
    [model, live],
  );

  const togglePlay = useCallback(() => {
    if (!live && !playing && tRef.current >= model.tEnd - 0.01) seek(model.t0);
    setPlaying((p) => !p);
    if (live && playing) setFollowLive(false);
  }, [live, playing, model, seek]);

  const selectCar = useCallback((c: number, follow = true) => {
    setSelected(c);
    if (follow) setCamMode("follow");
  }, []);

  const zoomBy = useCallback(
    (factor: number, sx?: number, sy?: number) => {
      const { w, h } = sizeRef.current;
      const st = stateRef.current;
      if (st.camMode === "follow") {
        followScaleRef.current = Math.max(0.3, Math.min(30, followScaleRef.current * factor));
        draw();
        return;
      }
      const cam = { ...camRef.current };
      const ax = sx ?? w / 2;
      const ay = sy ?? h / 2;
      const [wx, wy] = screenToWorld(cam, w, h, ax, ay);
      cam.scale = Math.max(0.05, Math.min(30, cam.scale * factor));
      // garder le point sous le curseur immobile
      cam.cx = wx - (ax - w / 2) / cam.scale;
      cam.cy = wy + (ay - h / 2) / cam.scale;
      camRef.current = cam;
      if (st.camMode !== "free") setCamMode("free");
      stateRef.current.camMode = "free";
      draw();
    },
    [draw],
  );

  // molette (écouteur non passif pour bloquer le défilement de la page)
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = canvas.getBoundingClientRect();
      zoomBy(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [zoomBy]);

  // glisser pour déplacer, cliquer pour sélectionner
  const dragRef = useRef<{ x: number; y: number; moved: boolean; id: number } | null>(null);
  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (e.pointerType === "touch" && stateRef.current.camMode === "overview") {
      dragRef.current = { x: e.clientX, y: e.clientY, moved: false, id: -1 };
      return;
    }
    dragRef.current = { x: e.clientX, y: e.clientY, moved: false, id: e.pointerId };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const d = dragRef.current;
    if (!d || d.id !== e.pointerId) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (!d.moved && Math.hypot(dx, dy) < 4) return;
    d.moved = true;
    d.x = e.clientX;
    d.y = e.clientY;
    const cam = { ...camRef.current };
    cam.cx -= dx / cam.scale;
    cam.cy += dy / cam.scale;
    camRef.current = cam;
    if (stateRef.current.camMode !== "free") {
      stateRef.current.camMode = "free";
      setCamMode("free");
    }
    draw();
  };
  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const d = dragRef.current;
    dragRef.current = null;
    if (!d || d.moved) return;
    const r = e.currentTarget.getBoundingClientRect();
    const { w, h } = sizeRef.current;
    const c = pickCar(visibleRef.current, camRef.current, w, h, e.clientX - r.left, e.clientY - r.top, e.pointerType === "touch" ? 26 : 18);
    if (c != null) setSelected(c);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).tagName === "INPUT" && (e.target as HTMLInputElement).type !== "range") return;
    if (e.key === " " || e.key === "k") {
      e.preventDefault();
      togglePlay();
    } else if (e.key === "ArrowRight" && (e.target as HTMLElement).tagName !== "INPUT") {
      seek(tRef.current + 5);
    } else if (e.key === "ArrowLeft" && (e.target as HTMLElement).tagName !== "INPUT") {
      seek(tRef.current - 5);
    }
  };

  // ------------------------------------------------------------ données d'interface
  const rows = useMemo(() => model.rowsAt(uiT), [model, uiT, version]);
  const lapInfo = useMemo(() => model.leaderLap(uiT), [model, uiT, version]);
  const hasFrames = model.frames.length > 0;
  const atLiveEdge = live && followLive;
  const sessionLabel = model.header.kind === "quali" ? "Qualifications" : "Course";

  let lapText: string;
  if (model.header.kind === "quali") lapText = "Tour chronométré";
  else if (lapInfo.phase === "grid") lapText = "Grille de départ";
  else if (lapInfo.phase === "finish") lapText = "Arrivée";
  else lapText = `Tour ${lapInfo.lap} / ${lapInfo.total}`;

  return (
    <section className="viewer" aria-label={`Replay — ${sessionLabel}`} onKeyDown={onKeyDown}>
      <div className="viewer-main">
        <div className="viewer-stage">
          <div className="viewer-canvas" ref={wrapRef}>
            <canvas
              ref={canvasRef}
              tabIndex={0}
              aria-label="Vue du circuit. Molette pour zoomer, glisser pour déplacer, cliquer sur une voiture pour la sélectionner."
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={() => (dragRef.current = null)}
              className={camMode === "overview" ? "" : "grabbable"}
            />
            {!hasFrames ? <div className="viewer-overlay">En attente des premières images…</div> : null}
            <div className="viewer-hud">
              <span className="hud-lap">{lapText}</span>
              <span className="hud-time">{fmtClock(uiT)}</span>
              {live ? <span className={atLiveEdge ? "hud-live on" : "hud-live"}>{atLiveEdge ? "EN DIRECT" : "EN DIFFÉRÉ"}</span> : null}
            </div>
            <div className="viewer-legend" aria-hidden>
              <span><i className="lg-edge" />Rebord (mur)</span>
              <span><i className="lg-pit" />Voie des stands</span>
              <span><i className="lg-car2" />Voiture n° 2</span>
            </div>
            <div className="viewer-zoom">
              <button type="button" onClick={() => zoomBy(1.35)} aria-label="Zoomer">+</button>
              <button type="button" onClick={() => zoomBy(1 / 1.35)} aria-label="Dézoomer">−</button>
            </div>
          </div>

          <div className="viewer-controls">
            <button type="button" className="btn btn-primary btn-play" onClick={togglePlay} disabled={!hasFrames} aria-label={playing ? "Pause" : "Lecture"}>
              {playing ? <PauseIcon /> : <PlayIcon />}
              <span>{playing ? "Pause" : "Lecture"}</span>
            </button>
            <div className="ctrl-time tabular">
              {fmtClock(uiT)} <span className="muted">/ {fmtClock(model.tEnd)}</span>
            </div>
            {live ? (
              <button type="button" className={`btn btn-ghost ${atLiveEdge ? "is-active" : ""}`} onClick={() => { setFollowLive(true); setPlaying(true); }} disabled={atLiveEdge}>
                {atLiveEdge ? "En direct" : "Revenir au direct"}
              </button>
            ) : (
              <div className="seg" role="group" aria-label="Vitesse de lecture">
                {SPEEDS.map((s) => (
                  <button key={s} type="button" className={s === speed ? "is-active" : ""} aria-pressed={s === speed} onClick={() => setSpeed(s)}>
                    ×{s}
                  </button>
                ))}
              </div>
            )}
            <div className="seg" role="group" aria-label="Caméra">
              <button type="button" className={camMode === "overview" ? "is-active" : ""} aria-pressed={camMode === "overview"} onClick={() => setCamMode("overview")}>
                Vue d&apos;ensemble
              </button>
              <button
                type="button"
                className={camMode === "follow" ? "is-active" : ""}
                aria-pressed={camMode === "follow"}
                onClick={() => setCamMode("follow")}
                disabled={selected == null}
              >
                Suivre
              </button>
            </div>
          </div>
          <Timeline model={model} t={uiT} version={version} onSeek={seek} />
        </div>

        <Leaderboard model={model} rows={rows} selected={selected} onSelect={(c) => selectCar(c)} lapText={lapText} />
      </div>

      <div className="viewer-bottom">
        <Telemetry model={model} car={selected} t={uiT} version={version} compounds={compounds} />
        <EventsTicker model={model} t={uiT} version={version} onSeek={seek} onSelect={(c) => selectCar(c, false)} />
      </div>
    </section>
  );
}

function PlayIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
      <path d="M3 1.5v11l9-5.5z" fill="currentColor" />
    </svg>
  );
}
function PauseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
      <path d="M3 1.5h3v11H3zM8 1.5h3v11H8z" fill="currentColor" />
    </svg>
  );
}
