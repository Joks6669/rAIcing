"use client";
// Page d'une rencontre : suivi de la simulation, puis replay façon télé et résultats.
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import ReplayLoader from "@/components/ReplayLoader";
import { CompoundBadge } from "@/components/ui";
import { fmtGapSeconds, fmtInt, fmtKm, fmtLapTime } from "@/lib/format";
import { qualiStatusLabel, raceStatusLabel } from "@/lib/labels";
import type { MatchMeta } from "./types";

const WEATHER: Record<string, string> = { dry: "sec", rain: "pluie", random: "au hasard" };

/** Écart renvoyé par le simulateur (« +7.180s », « +1 tour ») au format du site. */
function fmtGap(gap: string | null): string {
  if (!gap) return "—";
  const m = /^\+([\d.]+)s$/.exec(gap);
  return m ? fmtGapSeconds(Number(m[1])) : gap.replace("tour(s)", "tours");
}

export default function MatchView({ id }: { id: string }) {
  const [m, setM] = useState<MatchMeta | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const res = await fetch(`/rencontres-api/${id}`, { cache: "no-store" });
        const data = await res.json();
        if (stop) return;
        if (!res.ok) {
          setError(typeof data?.detail === "string" ? data.detail : `Erreur ${res.status}`);
          return;
        }
        setM(data);
        if (data.status === "queued" || data.status === "running") timer = setTimeout(load, 1500);
      } catch {
        if (!stop) timer = setTimeout(load, 3000);
      }
    };
    load();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [id]);

  const src = useCallback((session: "race" | "quali") => `/rencontres-api/${id}/replay?session=${session}`, [id]);

  if (error) {
    return (
      <div className="page-head">
        <div className="eyebrow">Rencontre</div>
        <h1>Introuvable</h1>
        <p className="lede">{error}</p>
        <Link href="/rencontres" className="link-arrow">
          Toutes les rencontres
        </Link>
      </div>
    );
  }
  if (!m) return <p className="muted">Chargement…</p>;

  const running = m.status === "queued" || m.status === "running";
  const colorOf = (car: number) => m.entrants[car]?.color ?? "#888";
  const winner = m.results?.race?.[0];

  return (
    <>
      <header className="page-head">
        <div className="eyebrow">
          <Link href="/rencontres">Rencontres</Link>
          <span>· {m.track.name}</span>
        </div>
        <h1>{m.name}</h1>
        <div className="meta-row">
          <span>
            <strong>{m.laps}</strong> tours
          </span>
          <span>
            <strong>{fmtKm(m.track.length)}</strong> par tour
          </span>
          <span>
            <strong>{m.track.corners}</strong> virages
          </span>
          <span>
            Météo <strong>{m.results ? (m.results.weather.rain ? "pluie" : "sec") : WEATHER[m.weather] ?? m.weather}</strong>
          </span>
          <span>
            Graine du circuit <strong>{m.track.seed}</strong>
          </span>
        </div>
      </header>

      {running ? (
        <div className="card match-progress">
          <div className="card-head">
            <span className="card-title">Simulation en cours</span>
            <span className="tabular">{Math.round(m.progress * 100)} %</span>
          </div>
          <div className="match-bar">
            <span style={{ width: `${Math.max(3, m.progress * 100)}%` }} />
          </div>
          <p className="small muted" style={{ margin: "10px 0 0" }}>
            {m.status === "queued" ? "En file d'attente…" : `Étape : ${m.stage}.`} Une course complète se simule en une à deux minutes ;
            le replay s&apos;affichera ici automatiquement.
          </p>
        </div>
      ) : null}

      {m.status === "failed" ? (
        <div className="note" role="alert" style={{ borderLeftColor: "var(--live)" }}>
          <strong>La simulation a échoué.</strong> {m.error}
        </div>
      ) : null}

      {m.status === "done" ? (
        <>
          {winner ? (
            <div className="match-winner" style={{ ["--c" as string]: colorOf(winner.car) }}>
              <span className="mw-label">Vainqueur</span>
              <span className="mw-name">{winner.driver}</span>
              <span className="mw-time tabular">{fmtLapTime(winner.time)}</span>
            </div>
          ) : null}
          <ReplayLoader roundId={0} sessions={{ race: true, quali: true }} src={src} />
        </>
      ) : null}

      <section className="section">
        <div className="section-title">
          <h2>Participants</h2>
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Pilote</th>
                <th>Type</th>
                <th className="num">Paramètres</th>
                <th>Commandes</th>
                <th>Départ</th>
              </tr>
            </thead>
            <tbody>
              {m.entrants.map((e, i) => (
                <tr key={i}>
                  <td className="who">
                    <span className="swatch" style={{ background: e.color }} aria-hidden /> {e.name}
                  </td>
                  <td>{e.kind === "brain" ? `Modèle${e.brain?.method ? ` (${e.brain.method})` : ""}` : "Pilote de référence"}</td>
                  <td className="num">{e.brain ? fmtInt(e.brain.params) : "—"}</td>
                  <td className="small">
                    {e.brain
                      ? e.brain.can_pit
                        ? `${e.brain.outputs} sorties : arrêts aux stands${e.brain.radio ? " et radio" : ""}`
                        : `${e.brain.outputs} sorties : volant et pédale (pas d'arrêt)`
                      : "complet"}
                  </td>
                  <td>
                    <CompoundBadge compound={m.results?.starts?.[`m${i}`] ?? (e.start === "auto" ? null : e.start)} size="sm" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {m.results ? (
        <div className="section match-results">
          <div>
            <div className="section-title">
              <h2>Course</h2>
            </div>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th className="ctr">Pos.</th>
                    <th>Pilote</th>
                    <th className="num">Temps / écart</th>
                    <th className="num">Meilleur tour</th>
                    <th className="num">Arrêts</th>
                  </tr>
                </thead>
                <tbody>
                  {m.results.race.map((r) => (
                    <tr key={r.car}>
                      <td className="ctr pos">{r.status === "finished" ? r.pos : "—"}</td>
                      <td className="who">
                        <span className="swatch" style={{ background: colorOf(r.car) }} aria-hidden /> {r.driver}
                        <small>
                          {raceStatusLabel(r.status, r.reason)}
                          {r.penalty > 0 ? ` · +${r.penalty} s de pénalité` : ""}
                        </small>
                      </td>
                      <td className="num">{r.pos === 1 && r.status === "finished" ? fmtLapTime(r.time) : r.status === "finished" ? fmtGap(r.gap) : "—"}</td>
                      <td className="num">{fmtLapTime(r.best_lap)}</td>
                      <td className="num">{r.pits}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div>
            <div className="section-title">
              <h2>Qualifications</h2>
            </div>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th className="ctr">Pos.</th>
                    <th>Pilote</th>
                    <th className="num">Temps</th>
                    <th className="num">Écart</th>
                  </tr>
                </thead>
                <tbody>
                  {m.results.quali.map((q) => (
                    <tr key={q.car}>
                      <td className="ctr pos">{q.pos}</td>
                      <td className="who">
                        <span className="swatch" style={{ background: colorOf(q.car) }} aria-hidden /> {q.driver}
                        {q.status !== "timed" ? <small>{qualiStatusLabel(q.status, q.reason)}</small> : null}
                      </td>
                      <td className="num">{fmtLapTime(q.time)}</td>
                      <td className="num">{q.gap ? fmtGapSeconds(q.gap) : q.pos === 1 ? "—" : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
