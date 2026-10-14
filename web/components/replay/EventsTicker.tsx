"use client";
import { useMemo } from "react";
import type { ReplayModel } from "@/lib/replay-model";
import { latestEvents } from "@/lib/replay-events";
import { fmtClock } from "@/lib/format";

export default function EventsTicker({
  model,
  t,
  version,
  onSeek,
  onSelect,
}: {
  model: ReplayModel;
  t: number;
  version: number;
  onSeek: (t: number) => void;
  onSelect: (car: number) => void;
}) {
  // arrondi à 0,2 s : évite de recalculer à chaque rafraîchissement
  const tq = Math.floor(t * 5) / 5;
  const items = useMemo(() => latestEvents(model, tq, 7), [model, tq, version]);
  return (
    <section className="panel ticker" aria-label="Événements de course">
      <div className="panel-head">
        <h3>Événements</h3>
      </div>
      {items.length === 0 ? (
        <p className="muted small">Aucun événement pour l&apos;instant.</p>
      ) : (
        <ol className="ticker-list" aria-live="polite">
          {items.map((e) => (
            <li key={e.key} className={`tk tk-${e.tone}`}>
              <button type="button" className="tk-time tabular" onClick={() => onSeek(e.t - 3)} title="Revoir ce moment">
                {fmtClock(e.t)}
              </button>
              <span className="tk-text">
                <strong>{e.title}</strong> — {e.detail}
              </span>
              {e.cars[0] != null ? (
                <button type="button" className="tk-sel" onClick={() => onSelect(e.cars[0])} aria-label="Sélectionner la voiture">
                  <span className="swatch" style={{ background: model.header.cars[e.cars[0]]?.color }} />
                </button>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
