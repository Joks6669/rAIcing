import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getRegulations, getRound, getSeason, getStandings, orNull } from "@/lib/api";
import { CATEGORY, CATEGORY_HINT, CONTACT_KIND, METHODS, ROUND_STATUS, ELIM_REASON_SHORT, qualiStatusLabel, raceStatusLabel } from "@/lib/labels";
import { fmtClock, fmtDateLong, fmtGapSeconds, fmtKm, fmtLapTime, fmtLapsDown, fmtPercent } from "@/lib/format";
import { CompoundBadge, EmptyState, RoundStatusPill, SectionTitle, TeamSwatch } from "@/components/ui";
import ReplayLoader from "@/components/ReplayLoader";
import type { RaceRow, Round } from "@/lib/types";

type Props = { params: Promise<{ id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  const round = await orNull(getRound(Number.parseInt(id, 10) || 0)).catch(() => null);
  if (!round) return { title: "Manche" };
  return { title: round.track ? `Manche ${round.number} — ${round.track.name}` : `Manche ${round.number}` };
}

export default async function CoursePage({ params }: Props) {
  const { id: raw } = await params;
  const id = Number.parseInt(raw, 10);
  if (!Number.isFinite(id)) notFound();
  const round = await orNull(getRound(id));
  if (!round) notFound();
  const [season, standings, regs] = await Promise.all([
    orNull(getSeason(round.season)),
    orNull(getStandings(round.season)),
    orNull(getRegulations(round.season)),
  ]);
  const summary = season?.rounds.find((r) => r.id === round.id);
  const colors: Record<string, string> = {};
  for (const t of standings?.teams ?? []) colors[t.team_id] = t.color;
  const prev = season?.rounds.find((r) => r.number === round.number - 1);
  const nextR = season?.rounds.find((r) => r.number === round.number + 1);

  const category = round.track?.category ?? round.category ?? summary?.category;
  const length = round.track?.length ?? round.track_length ?? summary?.track_length;
  const date = round.date ?? summary?.date ?? null;

  if (round.status !== "done" || !round.race) {
    return (
      <div className="container">
        <header className="page-head">
          <div className="eyebrow">
            <Link href={`/saison/${round.season}`}>Saison {round.season}</Link>
            <span>· Manche {round.number}</span>
            <RoundStatusPill status={round.status} />
          </div>
          <h1>Manche {round.number} — circuit secret</h1>
          <div className="meta-row">
            <span>{fmtDateLong(date)}</span>
            {category ? (
              <span>
                Catégorie <strong>{CATEGORY[category]}</strong> ({CATEGORY_HINT[category]})
              </span>
            ) : null}
            {length ? (
              <span>
                Longueur <strong>≈ {fmtKm(length)}</strong>
              </span>
            ) : null}
            <span>
              <strong>{round.laps}</strong> tours
            </span>
            {summary ? (
              <span>
                Risque de pluie <strong>{fmtPercent(summary.rain_probability)}</strong>
              </span>
            ) : null}
          </div>
        </header>
        <EmptyState title={round.status === "running" ? "Course en cours" : "Manche à venir"}>
          {round.status === "running" ? (
            <p>
              La manche se dispute en ce moment. <Link href="/direct">Suivre le direct</Link>
            </p>
          ) : (
            <p>
              Le tracé est généré à partir d&apos;une graine secrète. Il sera publié, avec les résultats et le replay, une fois la
              course terminée. Statut : {ROUND_STATUS[round.status].toLowerCase()}.
            </p>
          )}
        </EmptyState>
        <RoundNav season={round.season} prev={prev} next={nextR} />
      </div>
    );
  }

  const race = round.race;
  const cls = [...race.classification].sort((a, b) => a.pos - b.pos);
  const winner = cls[0];
  const names = new Map<string, string>();
  for (const r of cls) names.set(r.entry_id, r.driver);
  for (const r of round.quali?.classification ?? []) names.set(r.entry_id, r.driver);
  const grid = race.grid ?? [];
  const fastest = race.fastest_lap;

  return (
    <div className="layout-wide">
      <div className="container">
        <header className="page-head">
          <div className="eyebrow">
            <Link href={`/saison/${round.season}`}>Saison {round.season}</Link>
            <span>· Manche {round.number}</span>
            <RoundStatusPill status={round.status} />
          </div>
          <h1>{round.track?.name ?? "Circuit"}</h1>
          <div className="meta-row">
            <span>{fmtDateLong(date)}</span>
            <span>
              <strong>{round.laps}</strong> tours
            </span>
            {length ? (
              <span>
                <strong>{fmtKm(length)}</strong> par tour
              </span>
            ) : null}
            {round.track?.corners ? (
              <span>
                <strong>{round.track.corners}</strong> virages
              </span>
            ) : null}
            {category ? (
              <span>
                Catégorie <strong>{CATEGORY[category]}</strong>
              </span>
            ) : null}
            {round.weather ? (
              <span>
                Météo <strong>{round.weather.rain ? "pluie" : "sec"}</strong>, {round.weather.ambient.toLocaleString("fr-FR")} °C
              </span>
            ) : null}
          </div>
        </header>

        {round.has_replay.race || round.has_replay.quali ? (
          <ReplayLoader roundId={round.id} sessions={round.has_replay} compounds={regs?.regulations.compounds} />
        ) : (
          <EmptyState title="Replay indisponible">Le replay de cette manche n&apos;a pas été publié.</EmptyState>
        )}

        <section className="section" id="classement">
          <SectionTitle
            aside={
              fastest ? (
                <span>
                  Meilleur tour : <strong className="best-lap">{fmtLapTime(fastest.time)}</strong> ({fastest.driver})
                </span>
              ) : undefined
            }
          >
            Classement de la course
          </SectionTitle>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th className="pos">Pos</th>
                  <th>Pilote</th>
                  <th>Écurie</th>
                  <th className="num">Grille</th>
                  <th className="num">Tours</th>
                  <th className="num">Temps / écart</th>
                  <th className="num">Pénalité</th>
                  <th className="num">Meilleur tour</th>
                  <th className="num">Arrêts</th>
                  <th className="num">Points</th>
                  <th>Statut</th>
                </tr>
              </thead>
              <tbody>
                {cls.map((r) => {
                  const g = grid.indexOf(r.entry_id);
                  const isFl = fastest && fastest.entry_id === r.entry_id;
                  return (
                    <tr key={r.entry_id}>
                      <td className="pos">{r.status === "finished" ? r.pos : <span title={`Classé ${r.pos}e`}>{r.pos}</span>}</td>
                      <td>
                        <span className="who">
                          <TeamSwatch color={colors[r.team_id] ?? "var(--border-strong)"} />
                          <Link href={`/pilote/${r.driver_id}`}>{r.driver}</Link>
                        </span>
                      </td>
                      <td>
                        <Link href={`/ecurie/${r.team_id}`} className="muted">
                          {r.team}
                        </Link>
                      </td>
                      <td className="num muted">{g >= 0 ? g + 1 : "—"}</td>
                      <td className="num">{r.laps}</td>
                      <td className="num">{timeOrGap(r, winner)}</td>
                      <td className={r.penalty ? "num st-out" : "num faint"}>{r.penalty ? `+${r.penalty} s` : "—"}</td>
                      <td className={isFl ? "num best-lap" : "num"}>{fmtLapTime(r.best_lap)}</td>
                      <td className="num">{r.pits}</td>
                      <td className="num strong">{r.points || <span className="faint">0</span>}</td>
                      <td className={r.status === "finished" ? "reason st-ok" : "reason st-out"}>{raceStatusLabel(r.status, r.reason)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="small muted" style={{ marginTop: 8 }}>
            Les temps incluent les pénalités. Écarts mesurés sur le temps total ; voitures doublées : nombre de tours de retard.
          </p>
        </section>

        <div className="grid-2 section">
          <section>
            <SectionTitle aside="Un tour de lancement, un tour chronométré, seul en piste">Qualifications</SectionTitle>
            {round.quali ? (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th className="pos">Pos</th>
                      <th>Pilote</th>
                      <th className="ctr">Pneus</th>
                      <th className="num">Temps</th>
                      <th className="num">Écart</th>
                      <th>Statut</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...round.quali.classification]
                      .sort((a, b) => a.pos - b.pos)
                      .map((q) => (
                        <tr key={q.entry_id}>
                          <td className="pos">{q.pos}</td>
                          <td>
                            <span className="who">
                              <TeamSwatch color={colors[q.team_id] ?? "var(--border-strong)"} />
                              <span>
                                <Link href={`/pilote/${q.driver_id}`}>{q.driver}</Link>
                                <small> {q.team}</small>
                              </span>
                            </span>
                          </td>
                          <td className="ctr">
                            <CompoundBadge compound={q.compound} size="sm" />
                          </td>
                          <td className="num">{fmtLapTime(q.time)}</td>
                          <td className="num muted">{q.pos === 1 || q.gap == null ? "—" : fmtGapSeconds(q.gap)}</td>
                          <td className={q.status === "timed" ? "st-ok" : "st-out"}>{qualiStatusLabel(q.status, q.reason)}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="Pas de qualifications publiées" />
            )}
          </section>

          <section>
            <SectionTitle aside={`${race.incidents.length} contact${race.incidents.length > 1 ? "s" : ""}`}>Incidents et pénalités</SectionTitle>
            <IncidentsList round={round} names={names} penaltySeconds={Number(regs?.regulations.race.contact_time_penalty ?? 5)} />
            <h3 style={{ margin: "22px 0 10px" }}>Pénalités d&apos;écurie</h3>
            {Object.keys(race.team_penalties ?? {}).length ? (
              <ul className="incidents card" style={{ padding: 0 }}>
                {Object.entries(race.team_penalties).map(([team, pts]) => (
                  <li key={team}>
                    <span className="t">—</span>
                    <span>
                      <TeamSwatch color={colors[team] ?? "var(--border-strong)"} />{" "}
                      <Link href={`/ecurie/${team}`}>{cls.find((r) => r.team_id === team)?.team ?? team}</Link> : élimination
                      provoquée d&apos;un adversaire
                    </span>
                    <strong className="st-out">{`−${pts} pts`}</strong>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted small">
                Aucune. Une écurie perd 15 points lorsqu&apos;un de ses pilotes provoque l&apos;élimination d&apos;un adversaire (dans les 3
                secondes suivant un contact fautif).
              </p>
            )}
          </section>
        </div>

        {round.entries.length ? (
          <section className="section">
            <SectionTitle aside="Choisis par les IA directrices avant la course">Engagés et réglages</SectionTitle>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Pilote</th>
                    <th>Cerveau</th>
                    <th className="num">Aileron</th>
                    <th className="num">Freinage (avant)</th>
                    <th className="ctr">Pneus qualif.</th>
                    <th className="ctr">Pneus départ</th>
                  </tr>
                </thead>
                <tbody>
                  {round.entries.map((e) => (
                    <tr key={e.entry_id}>
                      <td>
                        <span className="who">
                          <TeamSwatch color={colors[e.team_id] ?? "var(--border-strong)"} />
                          <span>
                            <Link href={`/pilote/${e.driver_id}`}>{names.get(e.entry_id) ?? e.driver_id}</Link>
                            <small> {cls.find((r) => r.entry_id === e.entry_id)?.team ?? e.team_id}</small>
                          </span>
                        </span>
                      </td>
                      <td>
                        {e.brain ? (
                          <>
                            Génération {e.brain.generation} <span className="muted">· {METHODS[e.brain.method] ?? e.brain.method}</span>
                          </>
                        ) : (
                          <span className="muted">Pilote de référence</span>
                        )}
                      </td>
                      <td className="num">{e.setup.wing.toLocaleString("fr-FR", { minimumFractionDigits: 2 })}</td>
                      <td className="num">{fmtPercent(e.setup.brake_bias)}</td>
                      <td className="ctr">
                        <CompoundBadge compound={e.setup.quali_compound} size="sm" />
                      </td>
                      <td className="ctr">
                        <CompoundBadge compound={e.setup.start_compound} size="sm" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}

        <RoundNav season={round.season} prev={prev} next={nextR} />
      </div>
    </div>
  );
}

function timeOrGap(r: RaceRow, winner: RaceRow): string {
  if (r.status !== "finished") return "—";
  if (r === winner || r.pos === 1) return fmtLapTime(r.time);
  if (r.laps < winner.laps) return fmtLapsDown(winner.laps - r.laps);
  if (r.time != null && winner.time != null) return fmtGapSeconds(r.time - winner.time);
  return typeof r.gap === "string" && r.gap ? r.gap.replace(/s$/, " s") : "—";
}

function IncidentsList({ round, names, penaltySeconds }: { round: Round; names: Map<string, string>; penaltySeconds: number }) {
  const race = round.race!;
  type Item = { t: number; title: string; detail: string; sanction: string; tone: "ok" | "out" | "warn" };
  const items: Item[] = [];
  for (const inc of race.incidents) {
    const [a, b] = inc.cars.map((c) => names.get(c) ?? c);
    const kind = CONTACT_KIND[inc.kind] ?? inc.kind;
    items.push({
      t: inc.t,
      title: `Contact ${kind}`,
      detail: `${a} / ${b} — ${inc.fault ? `fautif : ${names.get(inc.fault) ?? inc.fault}` : "incident de course, pas de fautif"}`,
      sanction: inc.penalised
        ? `+${penaltySeconds}\u00a0s`
        : inc.fault
          ? `Sans sanction${inc.closing != null ? ` (rapprochement ${inc.closing.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} m/s)` : ""}`
          : "—",
      tone: inc.penalised ? "warn" : "ok",
    });
  }
  // abandons et disqualifications (événements de course)
  const carNames = new Map<number, string>();
  for (const r of race.classification) if (r.car != null) carNames.set(r.car, r.driver);
  for (const e of race.events ?? []) {
    if (e.type !== "out" && e.type !== "dsq") continue;
    items.push({
      t: e.t,
      title: e.type === "dsq" ? "Disqualification" : "Abandon",
      detail: `${carNames.get(e.car ?? -1) ?? "?"} — ${ELIM_REASON_SHORT[e.reason ?? ""] ?? e.reason ?? ""}`,
      sanction: e.type === "dsq" ? "Exclu" : "",
      tone: "out",
    });
  }
  // abandons sans événement détaillé
  if (!(race.events ?? []).some((e) => e.type === "out" || e.type === "dsq")) {
    for (const r of race.classification) {
      if (r.status === "finished") continue;
      items.push({ t: r.end_time ?? NaN, title: r.status === "dsq" ? "Disqualification" : "Abandon", detail: `${r.driver} — ${raceStatusLabel(r.status, r.reason)}`, sanction: "", tone: "out" });
    }
  }
  items.sort((x, y) => (x.t || 0) - (y.t || 0));
  if (!items.length) return <p className="muted small">Course sans incident.</p>;
  return (
    <ul className="incidents card" style={{ padding: 0 }}>
      {items.map((it, i) => (
        <li key={i}>
          <span className="t">{Number.isFinite(it.t) ? fmtClock(it.t) : "—"}</span>
          <span>
            <strong>{it.title}</strong>
            <br />
            <span className="muted">{it.detail}</span>
          </span>
          <span className={it.tone === "warn" ? "st-out nowrap" : it.tone === "out" ? "st-out nowrap" : "muted small"}>{it.sanction}</span>
        </li>
      ))}
    </ul>
  );
}

function RoundNav({
  season,
  prev,
  next,
}: {
  season: number;
  prev?: { id: number; number: number; track_name: string | null };
  next?: { id: number; number: number; track_name: string | null };
}) {
  return (
    <nav className="section" aria-label="Manches" style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
      {prev ? (
        <Link href={`/course/${prev.id}`} className="btn">
          ← Manche {prev.number}
          {prev.track_name ? ` — ${prev.track_name}` : ""}
        </Link>
      ) : (
        <span />
      )}
      <Link href={`/saison/${season}`} className="btn btn-ghost">
        Calendrier
      </Link>
      {next ? (
        <Link href={`/course/${next.id}`} className="btn">
          Manche {next.number}
          {next.track_name ? ` — ${next.track_name}` : ""} →
        </Link>
      ) : (
        <span />
      )}
    </nav>
  );
}
