"use client";
// Visionneuse de replay (canvas 2D) : circuit, voitures interpolées, caméra, classement en direct,
// télémétrie de la voiture sélectionnée, fil des événements. Fonctionne sur un replay complet
// ou sur un flux d'images ajoutées au fil de l'eau (page Direct).
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ReplayModel, STATUS } from "@/lib/replay-model";
import { fmtClock } from "@/lib/format";
import type { CompoundSpec } from "@/lib/types";
import { fitCamera, pickCar, screenToWorld, type Camera } from "./replay/draw";
import { buildHighlights, buildScenery, directorCamera, drawBroadcast, newDirector, type Highlight, type Scenery } from "./replay/broadcast";
import Leaderboard from "./replay/Leaderboard";
import Telemetry from "./replay/Telemetry";
import EventsTicker from "./replay/EventsTicker";
import Timeline from "./replay/Timeline";

const SPEEDS = [1, 2, 5, 10, 30];
const LIVE_DELAY = 0.45; // s de retard sur la dernière image reçue, pour interpoler
const LIGHTS_MS = 3600; // séquence des feux de départ (temps réel)
type CamMode = "tv" | "overview" | "follow" | "free";

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

export default function ReplayViewer({ model, live = false, compounds, initialSelected, initialSpeed = 2, autoplay = false }: ReplayViewerProps) {
  const subscribe = useCallback((cb: () => void) => model.subscribe(cb), [model]);
  const version = useSyncExternalStore(subscribe, () => model.version, () => model.version);

  const [playing, setPlaying] = useState(autoplay || live);
  const [speed, setSpeed] = useState(initialSpeed);
  const [followLive, setFollowLive] = useState(live);
  const [camMode, setCamMode] = useState<CamMode>("tv");
  const [lightsAt, setLightsAt] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(() => {
    if (initialSelected != null) return initialSelected;
    if (!model.frames.length) return null;
    const f0 = model.frames[0];
    const idx = f0.findIndex((d) => d && d[model.F.pos] === 0);
    return idx >= 0 ? idx : 0;
  });
  const [uiT, setUiT] = useState(() => (live ? model.tEnd : model.t0));

  const tRef = useRef(live ? model.tEnd : model.t0);
  const stateRef = useRef({ playing, speed, followLive, camMode, selected, lightsAt });
  stateRef.current = { playing, speed, followLive, camMode, selected, lightsAt };

  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sizeRef = useRef({ w: 800, h: 520, dpr: 1 });
  const camRef = useRef<Camera>({ cx: 0, cy: 0, scale: 1 });
  const followScaleRef = useRef(4);
  const visibleRef = useRef<{ x: number; y: number; car: number }[]>([]);
  const sceneRef = useRef<Scenery | null>(null);
  const directorRef = useRef(newDirector());
  const dtRealRef = useRef(0.016);
  const highlights = useMemo<Highlight[]>(() => buildHighlights(model), [model, version]);
  const highlightsRef = useRef(highlights);
  highlightsRef.current = highlights;

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
    if (!sceneRef.current) sceneRef.current = buildScenery(model.geom, model.anyWet);
    const { w, h, dpr } = sizeRef.current;
    const st = stateRef.current;
    const t = tRef.current;
    const now = performance.now();
    let cam: Camera;
    if (st.camMode === "tv") {
      cam = directorCamera(directorRef.current, model, t, now, w, h, highlightsRef.current, null, dtRealRef.current) ?? fitCamera(model.geom, w, h, 24, 20);
    } else if (st.camMode === "overview") {
      cam = fitCamera(model.geom, w, h, w < 500 ? 12 : 24, w < 500 ? 34 : 20);
    } else if (st.camMode === "follow" && st.selected != null) {
      const p = model.pose(st.selected, t);
      cam = p && p.status !== STATUS.pit ? { cx: p.x, cy: p.y, scale: followScaleRef.current } : { ...camRef.current, scale: followScaleRef.current };
    } else {
      cam = camRef.current;
    }
    camRef.current = cam;
    const focus = st.camMode === "tv" ? directorRef.current.target : st.selected;
    visibleRef.current = drawBroadcast({
      ctx, w, h, dpr, cam, model, scene: sceneRef.current, t, now,
      selected: st.camMode === "tv" ? null : st.selected, focus, shortNames, colors, teammateIndex,
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

  // boucle d'animation
  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    let lastUi = 0;
    const loop = (now: number) => {
      const dtReal = Math.min(0.1, (now - last) / 1000);
      last = now;
      dtRealRef.current = dtReal;
      const st = stateRef.current;
      if (st.lightsAt != null) {
        // feux de départ : le temps de course reste figé jusqu'à l'extinction
        if (now - st.lightsAt >= LIGHTS_MS) {
          stateRef.current.lightsAt = null;
          setLightsAt(null);
        }
      } else if (live && st.followLive) {
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
    sceneRef.current = null;
    directorRef.current = newDirector();
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
    if (!live && !playing && model.header.kind !== "quali" && tRef.current <= model.t0 + 0.05) {
      const now = performance.now();
      stateRef.current.lightsAt = now;
      setLightsAt(now);
    }
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
    if (e.pointerType === "touch" && (stateRef.current.camMode === "overview" || stateRef.current.camMode === "tv")) {
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
    if (c != null) selectCar(c);
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
  const focusCar = camMode === "tv" ? directorRef.current.target : selected;
  const focusRow = focusCar != null ? rows.find((r) => r.car === focusCar) ?? null : null;
  const banner = useMemo(() => {
    let b: Highlight | null = null;
    for (const hl of highlights) {
      if (hl.t > uiT) break;
      if (uiT - hl.t < 3.6) b = hl;
    }
    return b;
  }, [highlights, uiT]);
  const rainNow = useMemo(() => model.wetnessAt(uiT)?.rain ?? 0, [model, uiT]);

  let lapText: string;
  if (model.header.kind === "quali") lapText = "Tour chronométré";
  else if (lapInfo.phase === "grid") lapText = "Grille de départ";
  else if (lapInfo.phase === "finish") lapText = "Arrivée";
  else lapText = `Tour ${lapInfo.lap} / ${lapInfo.total}`;

  return (
    <section className="viewer viewer-tv" aria-label={`Replay — ${sessionLabel}`} onKeyDown={onKeyDown}>
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
              className={camMode === "overview" || camMode === "tv" ? "" : "grabbable"}
            />
            {!hasFrames ? <div className="viewer-overlay">En attente des premières images…</div> : null}
            <TvTower model={model} rows={rows} colors={colors} shortNames={shortNames} focus={focusCar} onSelect={(c) => selectCar(c)} />
            <div className="tv-lapbox">
              <div className="tv-lapbox-main">
                <span className="tv-lapbox-label">{model.header.kind === "quali" ? "QUALIFS" : lapInfo.phase === "race" ? "TOUR" : lapInfo.phase === "finish" ? "ARRIVÉE" : "GRILLE"}</span>
                {model.header.kind !== "quali" && lapInfo.phase === "race" ? (
                  <span className="tv-lapbox-lap tabular">{lapInfo.lap}<small>/{lapInfo.total}</small></span>
                ) : null}
              </div>
              <div className="tv-lapbox-sub tabular">
                {fmtClock(uiT)}
                {rainNow > 0.05 ? <span className="tv-rain" title="Pluie">PLUIE</span> : null}
                {live ? <span className={atLiveEdge ? "hud-live on" : "hud-live"}>{atLiveEdge ? "DIRECT" : "DIFFÉRÉ"}</span> : null}
              </div>
            </div>
            {banner ? (
              <div key={`${banner.t}-${banner.kind}`} className={`tv-banner tv-${banner.kind}`} role="status">
                <span className="tv-banner-title">{banner.title}</span>
                <span className="tv-banner-text">{banner.text}</span>
              </div>
            ) : null}
            {focusRow ? (
              <div className="tv-card" style={{ ["--team" as string]: colors[focusRow.car] }}>
                <span className="tv-card-pos tabular">{focusRow.status >= 3 ? "—" : `P${focusRow.pos + 1}`}</span>
                <span className="tv-card-name">
                  <b>{cars[focusRow.car].driver}</b>
                  <small>{cars[focusRow.car].team}</small>
                </span>
                <span className="tv-card-speed tabular">
                  {focusRow.status === STATUS.pit ? "STANDS" : Math.round(focusRow.speed * 3.6)}
                  {focusRow.status === STATUS.pit ? null : <small>km/h</small>}
                </span>
                <span className={`tv-card-tyre tyre-${model.header.compounds[focusRow.compound] ?? "medium"}`}>
                  {(model.header.compounds[focusRow.compound] ?? "?").slice(0, 1).toUpperCase()}
                  <small className="tabular">{Math.round(focusRow.wear * 100)} %</small>
                </span>
              </div>
            ) : null}
            {lightsAt != null ? <StartLights at={lightsAt} /> : null}
            {!playing && !live && hasFrames && uiT <= model.t0 + 0.05 ? (
              <button type="button" className="tv-start" onClick={togglePlay}>
                <PlayIcon />
                <span>{model.header.kind === "quali" ? "Regarder les qualifications" : "Regarder la course"}</span>
              </button>
            ) : null}
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
              <button type="button" className={camMode === "tv" ? "is-active" : ""} aria-pressed={camMode === "tv"} onClick={() => setCamMode("tv")}>
                Réalisation TV
              </button>
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

      </div>

      <div className="viewer-bottom">
        <Leaderboard model={model} rows={rows} selected={selected} onSelect={(c) => selectCar(c)} lapText={lapText} />
        <Telemetry model={model} car={selected} t={uiT} version={version} compounds={compounds} />
        <EventsTicker model={model} t={uiT} version={version} onSeek={seek} onSelect={(c) => selectCar(c, false)} />
      </div>
    </section>
  );
}

function TvTower({ model, rows, colors, shortNames, focus, onSelect }: {
  model: ReplayModel; rows: ReturnType<ReplayModel["rowsAt"]>; colors: string[]; shortNames: string[]; focus: number | null; onSelect: (c: number) => void;
}) {
  const sorted = [...rows].sort((a, b) => a.pos - b.pos);
  if (!sorted.length) return null;
  return (
    <ol className="tv-tower" aria-label="Classement en direct">
      {sorted.map((r) => {
        const out = r.status === STATUS.dnf || r.status === STATUS.dsq;
        const gap = out ? (r.status === STATUS.dsq ? "DSQ" : "OUT") : r.status === STATUS.pit ? "STANDS" : r.pos === 0 ? (r.finished ? "VAINQ." : "TÊTE") : r.gapText.replace("\u00a0s", "").replace(" tours", "T").replace(" tour", "T");
        return (
          <li key={r.car} className={`${out ? "is-out" : ""} ${r.car === focus ? "is-focus" : ""}`}>
            <button type="button" onClick={() => onSelect(r.car)} title={model.header.cars[r.car].driver}>
              <span className="tv-pos tabular">{r.pos + 1}</span>
              <span className="tv-bar" style={{ background: colors[r.car] }} />
              <span className="tv-name">{shortNames[r.car]}</span>
              <span className="tv-gap tabular">{gap}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function StartLights({ at }: { at: number }) {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      setNow(performance.now());
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  const el = now - at;
  const on = Math.min(5, Math.floor(el / 600) + 1);
  const out = el >= 3300;
  return (
    <div className={`tv-lights ${out ? "is-out" : ""}`} aria-hidden>
      {[0, 1, 2, 3, 4].map((i) => (
        <span key={i} className={!out && i < on ? "on" : ""}>
          <i />
          <i />
        </span>
      ))}
      {out ? <b>C&apos;EST PARTI !</b> : null}
    </div>
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
