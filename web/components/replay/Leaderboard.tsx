"use client";
import { memo } from "react";
import { STATUS, type CarRow, type ReplayModel } from "@/lib/replay-model";
import { CompoundBadge } from "../ui";

function statusCell(r: CarRow) {
  switch (r.status) {
    case STATUS.pit:
      return <span className="lb-st lb-st-pit">AUX STANDS</span>;
    case STATUS.finished:
      return <span className="lb-st lb-st-fin">Arrivé</span>;
    case STATUS.dnf:
      return <span className="lb-st lb-st-out">Abandon</span>;
    case STATUS.dsq:
      return <span className="lb-st lb-st-out">DSQ</span>;
    default:
      return <span className="lb-st lb-st-run">En piste</span>;
  }
}

function Leaderboard({
  model,
  rows,
  selected,
  onSelect,
  lapText,
}: {
  model: ReplayModel;
  rows: CarRow[];
  selected: number | null;
  onSelect: (car: number) => void;
  lapText: string;
}) {
  const cars = model.header.cars;
  return (
    <aside className="lb" aria-label="Classement en direct">
      <div className="lb-head">
        <span>Classement</span>
        <span className="muted">{lapText}</span>
      </div>
      <div className="lb-cols" aria-hidden>
        <span>Pos</span>
        <span />
        <span>Pilote</span>
        <span className="ta-r">Écart</span>
        <span>Pneus</span>
        <span className="ta-c" title="Arrêts aux stands">Arr.</span>
        <span>Statut</span>
      </div>
      <ol className="lb-list">
        {rows.map((r) => {
          const car = cars[r.car];
          const out = r.status === STATUS.dnf || r.status === STATUS.dsq;
          const compound = model.header.compounds[r.compound];
          return (
            <li key={r.car}>
              <button
                type="button"
                className={`lb-row${selected === r.car ? " is-selected" : ""}${out ? " is-out" : ""}`}
                onClick={() => onSelect(r.car)}
                aria-pressed={selected === r.car}
                title={`${car.driver} — ${car.team}`}
              >
                <span className="lb-pos tabular">{r.pos + 1}</span>
                <span className="lb-bar" style={{ background: car.color }} />
                <span className="lb-name">
                  <span className="lb-driver">{car.driver}</span>
                  <span className="lb-team">{car.team}</span>
                </span>
                <span className="lb-gap tabular">{r.gapText}</span>
                <span className="lb-tyre">
                  <CompoundBadge compound={compound} size="sm" />
                  <span className="tabular">{Math.round(r.wear * 100)} %</span>
                </span>
                <span className="lb-pits tabular">{r.pits}</span>
                {statusCell(r)}
              </button>
            </li>
          );
        })}
      </ol>
    </aside>
  );
}

export default memo(Leaderboard);
