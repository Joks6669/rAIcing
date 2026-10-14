"use client";
import { useMemo } from "react";
import type { ReplayModel } from "@/lib/replay-model";
import { timelineMarkers } from "@/lib/replay-events";
import { fmtClock } from "@/lib/format";

export default function Timeline({
  model,
  t,
  version,
  onSeek,
}: {
  model: ReplayModel;
  t: number;
  version: number;
  onSeek: (t: number) => void;
}) {
  const t0 = model.t0;
  const t1 = Math.max(model.tEnd, t0 + 1);
  // `version` change à chaque image reçue en direct : recalcul des marqueurs
  const markers = useMemo(() => timelineMarkers(model), [model, version]);
  const pct = (x: number) => ((x - t0) / (t1 - t0)) * 100;

  // repères de tours du leader
  const laps = useMemo(() => {
    const out: { t: number; lap: number }[] = [];
    const F = model.F;
    let prev = -Infinity;
    for (let k = 0; k < model.frames.length; k++) {
      let mx = -Infinity;
      for (const d of model.frames[k]) if (d && d[F.lap] > mx) mx = d[F.lap];
      if (mx > prev && mx >= 1 && Number.isFinite(prev)) out.push({ t: model.times[k], lap: mx });
      prev = mx;
    }
    return out.filter((l) => l.lap < model.header.laps);
  }, [model, version]);

  return (
    <div className="timeline">
      <div className="tl-track" aria-hidden>
        <div className="tl-progress" style={{ width: `${pct(t)}%` }} />
        {laps.map((l) => (
          <span key={`lap-${l.lap}`} className="tl-lap" style={{ left: `${pct(l.t)}%` }} title={`Tour ${l.lap + 1}`} />
        ))}
        {markers.map((m, i) => (
          <button
            key={i}
            type="button"
            tabIndex={-1}
            className={`tl-marker tl-${m.tone}`}
            style={{ left: `${pct(m.t)}%` }}
            title={`${fmtClock(m.t)} · ${m.label}`}
            onClick={() => onSeek(m.t - 3)}
          />
        ))}
      </div>
      <input
        type="range"
        className="tl-range"
        min={t0}
        max={t1}
        step={0.1}
        value={Math.min(t1, Math.max(t0, t))}
        onChange={(e) => onSeek(parseFloat(e.target.value))}
        aria-label="Position dans la course"
        aria-valuetext={fmtClock(t)}
      />
      <div className="tl-legend">
        <span><i className="tl-dot tl-contact" />Contact</span>
        <span><i className="tl-dot tl-pit" />Arrêt</span>
        <span><i className="tl-dot tl-out" />Abandon</span>
        <span><i className="tl-tick" />Tour</span>
      </div>
    </div>
  );
}
