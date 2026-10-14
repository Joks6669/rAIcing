"use client";
import Link from "next/link";
import { useMemo } from "react";
import { STATUS, type ReplayModel } from "@/lib/replay-model";
import { COMPOUND_NAME } from "@/lib/labels";
import { fmtClock, ordinal } from "@/lib/format";
import type { CompoundSpec } from "@/lib/types";
import regulationsDefault from "@/lib/regulations-default.json";
import { CompoundBadge } from "../ui";

const W = 300;
const H = 64;
const DEFAULT_COMPOUNDS = (regulationsDefault as unknown as { compounds: Record<string, CompoundSpec> }).compounds;
const COMPOUND_STROKE: Record<string, string> = {
  soft: "var(--tyre-soft)",
  medium: "var(--tyre-medium)",
  hard: "var(--tyre-hard-line)",
  wet: "var(--tyre-wet)",
};

function pathFrom(t: number[], v: number[], t0: number, t1: number, y0: number, y1: number): string {
  let d = "";
  let pen = false;
  for (let i = 0; i < t.length; i++) {
    const val = v[i];
    if (!Number.isFinite(val)) {
      pen = false;
      continue;
    }
    const x = ((t[i] - t0) / (t1 - t0 || 1)) * W;
    const y = H - ((Math.max(y0, Math.min(y1, val)) - y0) / (y1 - y0 || 1)) * H;
    d += `${pen ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    pen = true;
  }
  return d;
}

function Chart({
  title,
  value,
  range,
  children,
}: {
  title: string;
  value: React.ReactNode;
  range: string;
  children: React.ReactNode;
}) {
  return (
    <figure className="chart">
      <figcaption>
        <span className="chart-title">{title}</span>
        <span className="chart-value tabular">{value}</span>
      </figcaption>
      <div className="chart-box">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={title}>
          {children}
        </svg>
        <span className="chart-range">{range}</span>
      </div>
    </figure>
  );
}

function Cursor({ x }: { x: number }) {
  return <line x1={x} x2={x} y1={0} y2={H} className="chart-cursor" vectorEffect="non-scaling-stroke" />;
}

export default function Telemetry({
  model,
  car,
  t,
  version,
  compounds,
}: {
  model: ReplayModel;
  car: number | null;
  t: number;
  version: number;
  compounds?: Record<string, CompoundSpec>;
}) {
  const specs = compounds ?? DEFAULT_COMPOUNDS;
  // séries complètes de la voiture (recalculées en direct quand de nouvelles images arrivent)
  const vq = model.live ? Math.floor(version / 10) : 0;
  const data = useMemo(() => {
    if (car == null || !model.frames.length) return null;
    const speed = model.series(car, "speed");
    const wear = model.series(car, "wear");
    const temp = model.series(car, "temp");
    return { speed, wear, temp };
  }, [model, car, vq]);

  if (car == null || !data) {
    return (
      <section className="panel telemetry">
        <div className="panel-head">
          <h3>Télémétrie</h3>
        </div>
        <p className="muted small">Sélectionnez une voiture dans le classement ou sur la piste pour afficher sa télémétrie.</p>
      </section>
    );
  }

  const info = model.header.cars[car];
  const k = Math.max(0, model.indexAt(t));
  const F = model.F;
  const cur = model.frames[k]?.[car] ?? [];
  const st = cur[F.status];
  const compound = model.header.compounds[cur[F.compound]] ?? "medium";
  const t0 = model.t0;
  const t1 = Math.max(model.tEnd, t0 + 1);
  const cx = ((t - t0) / (t1 - t0)) * W;

  const spKmh = data.speed.v.map((v) => v * 3.6);
  const maxSp = Math.max(100, Math.ceil(Math.max(0, ...spKmh.filter(Number.isFinite)) / 50) * 50);

  // usure : un tracé par relais (couleur de la gomme)
  const wearSegs: { comp: string; t: number[]; v: number[] }[] = [];
  data.wear.t.forEach((tt, i) => {
    const comp = model.header.compounds[data.wear.compound[i]] ?? "medium";
    let seg = wearSegs[wearSegs.length - 1];
    if (!seg || seg.comp !== comp || (i > 0 && !Number.isFinite(data.wear.v[i - 1]) && Number.isFinite(data.wear.v[i]))) {
      seg = { comp, t: [], v: [] };
      wearSegs.push(seg);
    }
    seg.t.push(tt);
    seg.v.push(data.wear.v[i] * 100);
  });

  const temps = data.temp.v.filter(Number.isFinite);
  const spec = specs[compound];
  const tMin = Math.min(40, ...temps.map((x) => x - 5));
  const tMax = Math.max(130, ...temps.map((x) => x + 5));
  const bandLo = spec ? spec.t_opt - spec.t_width : null;
  const bandHi = spec ? spec.t_opt + spec.t_width : null;
  const yOf = (v: number) => H - ((v - tMin) / (tMax - tMin)) * H;

  // pédale : fenêtre glissante de 20 s
  const win = 20;
  const ka = Math.max(0, model.indexAt(t - win));
  const bars: { x: number; v: number }[] = [];
  for (let j = ka; j <= k; j++) {
    const d = model.frames[j]?.[car];
    if (!d) continue;
    bars.push({ x: ((model.times[j] - (t - win)) / win) * W, v: d[F.pedal] ?? 0 });
  }
  const pedal = cur[F.pedal] ?? 0;
  const bw = Math.max(1, W / (win / (model.header.dt || 0.2)) - 0.4);

  const statusText =
    st === STATUS.pit ? "Aux stands" : st === STATUS.finished ? "Arrivé" : st === STATUS.dnf ? "Abandon" : st === STATUS.dsq ? "Disqualifié" : "En piste";

  return (
    <section className="panel telemetry" aria-label={`Télémétrie de ${info.driver}`}>
      <div className="panel-head">
        <h3>
          <span className="swatch" style={{ background: info.color }} /> {info.driver}
          <span className="muted"> · {info.team}</span>
        </h3>
        <Link href={`/pilote/${info.driver_id}`} className="small">
          Fiche pilote
        </Link>
      </div>
      <dl className="readouts">
        <div>
          <dt>Position</dt>
          <dd className="tabular">{Number.isFinite(cur[F.pos]) ? ordinal(cur[F.pos] + 1) : "—"}</dd>
        </div>
        <div>
          <dt>Vitesse</dt>
          <dd className="tabular">{st === STATUS.pit ? "—" : `${Math.round((cur[F.speed] ?? 0) * 3.6)} km/h`}</dd>
        </div>
        <div>
          <dt>Pneus</dt>
          <dd className="tabular">
            <CompoundBadge compound={compound} size="sm" /> {Math.round((cur[F.wear] ?? 0) * 100)} %
          </dd>
        </div>
        <div>
          <dt>Température</dt>
          <dd className="tabular">{Math.round(cur[F.temp] ?? 0)} °C</dd>
        </div>
        <div>
          <dt>Dégâts</dt>
          <dd className="tabular">{Math.round((cur[F.damage] ?? 0) * 100)} %</dd>
        </div>
        <div>
          <dt>Statut</dt>
          <dd>{statusText}</dd>
        </div>
      </dl>
      <div className="charts">
        <Chart title="Vitesse" value={`${st === STATUS.pit ? "—" : Math.round((cur[F.speed] ?? 0) * 3.6)} km/h`} range={`0–${maxSp} km/h · course entière`}>
          <path d={pathFrom(data.speed.t, spKmh, t0, t1, 0, maxSp)} className="chart-line" vectorEffect="non-scaling-stroke" />
          <Cursor x={cx} />
        </Chart>
        <Chart
          title="Pédale"
          value={pedal >= 0 ? `Gaz ${Math.round(pedal * 100)} %` : `Frein ${Math.round(-pedal * 100)} %`}
          range={`Accélérateur / frein · ${win} dernières s`}
        >
          <line x1={0} x2={W} y1={H / 2} y2={H / 2} className="chart-axis" vectorEffect="non-scaling-stroke" />
          {bars.map((b, i) => (
            <rect
              key={i}
              x={b.x - bw / 2}
              width={bw}
              y={b.v >= 0 ? H / 2 - (b.v * H) / 2 : H / 2}
              height={(Math.abs(b.v) * H) / 2}
              className={b.v >= 0 ? "bar-gas" : "bar-brake"}
            />
          ))}
        </Chart>
        <Chart title="Usure des pneus" value={`${Math.round((cur[F.wear] ?? 0) * 100)} %`} range="0–100 % · par relais">
          {wearSegs.map((s, i) => (
            <path key={i} d={pathFrom(s.t, s.v, t0, t1, 0, 100)} style={{ stroke: COMPOUND_STROKE[s.comp] }} className="chart-line chart-line-plain" vectorEffect="non-scaling-stroke" />
          ))}
          <Cursor x={cx} />
        </Chart>
        <Chart
          title="Température des pneus"
          value={`${Math.round(cur[F.temp] ?? 0)} °C`}
          range={spec ? `Fenêtre ${COMPOUND_NAME[compound]?.toLowerCase() ?? compound} : ${bandLo}–${bandHi} °C` : `${tMin}–${tMax} °C`}
        >
          {bandLo != null && bandHi != null ? (
            <rect x={0} width={W} y={yOf(Math.min(tMax, bandHi))} height={yOf(Math.max(tMin, bandLo)) - yOf(Math.min(tMax, bandHi))} className="chart-band" />
          ) : null}
          <path d={pathFrom(data.temp.t, data.temp.v, t0, t1, tMin, tMax)} className="chart-line chart-line-temp" vectorEffect="non-scaling-stroke" />
          <Cursor x={cx} />
        </Chart>
      </div>
      <p className="muted small chart-foot">Temps de course : {fmtClock(t)}</p>
    </section>
  );
}
