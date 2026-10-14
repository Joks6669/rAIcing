import Link from "next/link";
import { getLeague, getLive, getRound, getSeason, getStandings, orNull } from "@/lib/api";
import { CATEGORY, CATEGORY_HINT, SEASON_STATUS } from "@/lib/labels";
import { fmtDateLong, fmtGapSeconds, fmtKm, fmtLapTime, fmtLapsDown, fmtPercent, fmtPts } from "@/lib/format";
import { RoundStatusPill, SectionTitle, Stat, TeamSwatch, Pill, EmptyState } from "@/components/ui";
import type { RaceRow, Round, RoundSummary, Standings } from "@/lib/types";

export default async function HomePage() {
  const league = await getLeague();
  const n = league.current_season;
  const [season, standings, live] = await Promise.all([getSeason(n), getStandings(n), getLive()]);
  const done = season.rounds.filter((r) => r.status === "done");
  const last = done[done.length - 1] ?? null;
  const next = season.rounds.find((r) => r.status !== "done") ?? null;
  const lastRound = last ? await orNull(getRound(last.id)) : null;
  const leader = standings.drivers[0];
  const teamColors: Record<string, string> = {};
  for (const t of standings.teams) teamColors[t.team_id] = t.color;

  return (
    <div className="container">
      <section className="hero">
        <div>
          <div className="eyebrow">
            <span>Saison {season.number}</span>
            <Pill tone={season.status === "active" ? "accent" : "neutral"}>{SEASON_STATUS[season.status] ?? season.status}</Pill>
          </div>
          <h1>{league.name}</h1>
          <p className="concept">
            Les IA dirigent, construisent, apprennent et pilotent. <em>L&apos;humain observe.</em>
          </p>
          <p className="muted" style={{ maxWidth: 620 }}>
            Huit écuries dirigées par des intelligences artificielles s&apos;affrontent sur des circuits inédits, avec des pilotes
            qui sont des réseaux de neurones entraînés par leurs soins. Aucune intervention humaine entre le départ et l&apos;arrivée.
          </p>
        </div>
        <div className="season-progress card">
          <div className="card-head" style={{ marginBottom: 4 }}>
            <span className="card-title">Avancement de la saison</span>
            <span className="tabular small muted">
              {done.length} / {season.rounds.length} manches
            </span>
          </div>
          <div className="progress-steps" aria-hidden>
            {season.rounds.map((r) => (
              <span key={r.id} className={r.status === "done" ? "done" : r.id === next?.id ? "next" : ""} />
            ))}
          </div>
          {leader && leader.points > 0 ? (
            <p className="small" style={{ margin: "8px 0 0" }}>
              En tête du championnat : <TeamSwatch color={leader.color} />{" "}
              <Link href={`/pilote/${leader.driver_id}`}>
                <strong>{leader.driver}</strong>
              </Link>{" "}
              <span className="muted">
                ({leader.team}, {fmtPts(leader.points)})
              </span>
            </p>
          ) : (
            <p className="small muted" style={{ margin: "8px 0 0" }}>
              Aucune manche courue pour l&apos;instant.
            </p>
          )}
        </div>
      </section>

      {live.live ? (
        <div className="note" style={{ marginBottom: 24, borderLeftColor: "var(--live)" }}>
          <strong>Course en direct.</strong> La manche est en cours.{" "}
          <Link href="/direct" className="link-arrow">
            Suivre le direct
          </Link>
        </div>
      ) : null}

      <div className="grid-2">
        <NextRoundCard round={next} />
        <LastRaceCard summary={last} round={lastRound} colors={teamColors} />
      </div>

      <section className="section">
        <SectionTitle aside={<Link href={`/saison/${season.number}`} className="link-arrow">Classements complets</Link>}>
          Championnat
        </SectionTitle>
        <div className="grid-2">
          <MiniDrivers standings={standings} />
          <MiniTeams standings={standings} />
        </div>
      </section>
    </div>
  );
}

function NextRoundCard({ round }: { round: RoundSummary | null }) {
  if (!round) {
    return (
      <div className="card">
        <div className="card-title">Prochaine manche</div>
        <EmptyState title="Saison terminée">Toutes les manches ont été courues.</EmptyState>
      </div>
    );
  }
  return (
    <div className="card next-round">
      <div className="card-head">
        <span className="card-title">Prochaine manche</span>
        <RoundStatusPill status={round.status} />
      </div>
      <h2>Manche {round.number}</h2>
      <p className="muted" style={{ margin: "4px 0 0" }}>
        {fmtDateLong(round.date)}
      </p>
      <dl>
        <div>
          <dt>Catégorie</dt>
          <dd>{CATEGORY[round.category]}</dd>
          <dd className="small muted" style={{ fontWeight: 400 }}>
            {CATEGORY_HINT[round.category]}
          </dd>
        </div>
        <div>
          <dt>Longueur</dt>
          <dd>environ {fmtKm(round.track_length)}</dd>
        </div>
        <div>
          <dt>Course</dt>
          <dd>{round.laps} tours</dd>
        </div>
        <div>
          <dt>Risque de pluie</dt>
          <dd>{fmtPercent(round.rain_probability)}</dd>
        </div>
      </dl>
      <div className="secret">
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
          <rect x="3" y="7" width="10" height="7" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M5.5 7V5a2.5 2.5 0 015 0v2" fill="none" stroke="currentColor" strokeWidth="1.5" />
        </svg>
        <span>Circuit inédit : son nom et son tracé ne seront révélés qu&apos;après la course.</span>
      </div>
    </div>
  );
}

function raceGap(row: RaceRow, winner: RaceRow): string {
  if (row.laps < winner.laps) return fmtLapsDown(winner.laps - row.laps);
  if (row.time != null && winner.time != null) return fmtGapSeconds(row.time - winner.time);
  return typeof row.gap === "string" && row.gap ? row.gap : "—";
}

function LastRaceCard({ summary, round, colors }: { summary: RoundSummary | null; round: Round | null; colors: Record<string, string> }) {
  if (!summary || !round?.race) {
    return (
      <div className="card">
        <div className="card-title">Dernière course</div>
        <EmptyState title="Aucune course disputée">Le premier départ sera donné lors de la manche 1.</EmptyState>
      </div>
    );
  }
  const cls = [...round.race.classification].sort((a, b) => a.pos - b.pos);
  const winner = cls[0];
  const podium = cls.filter((r) => r.status === "finished").slice(0, 3);
  const dnf = cls.filter((r) => r.status === "dnf").length;
  const dsq = cls.filter((r) => r.status === "dsq").length;
  const penalties = cls.filter((r) => r.penalty > 0);
  const penaltySeconds = cls.reduce((s, r) => s + (r.penalty || 0), 0);
  const teamPen = Object.values(round.race.team_penalties ?? {}).reduce((s, v) => s + v, 0);
  const fl = round.race.fastest_lap;
  const order = podium.length === 3 ? [podium[1], podium[0], podium[2]] : podium;
  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">Dernière course</span>
        <Link href={`/course/${round.id}`} className="small link-arrow">
          Revoir la course
        </Link>
      </div>
      <h2>
        Manche {round.number} — {round.track?.name ?? summary.track_name}
      </h2>
      <p className="muted" style={{ margin: "4px 0 16px" }}>
        {fmtDateLong(summary.date)} · {round.laps} tours
      </p>
      <div className="podium">
        {order.map((r) => (
          <PodiumStep key={r.entry_id} row={r} winner={winner} color={colors[r.team_id]} />
        ))}
      </div>
      <div className="facts">
        <Stat
          label="Meilleur tour"
          value={fl ? fmtLapTime(fl.time) : "—"}
          sub={fl ? fl.driver : undefined}
        />
        <Stat label="Abandons" value={dnf + dsq} sub={dsq ? `dont ${dsq} disqualification${dsq > 1 ? "s" : ""}` : dnf ? undefined : "tous à l'arrivée"} />
        <Stat
          label="Pénalités"
          value={penalties.length}
          sub={
            penalties.length || teamPen
              ? [penaltySeconds ? `+${penaltySeconds} s au total` : null, teamPen ? `−${teamPen} pts d'écurie` : null].filter(Boolean).join(" · ")
              : "aucune"
          }
        />
      </div>
    </div>
  );
}

function PodiumStep({ row, winner, color }: { row: RaceRow; winner: RaceRow; color?: string }) {
  return (
    <div className={`podium-step p${row.pos}`} style={{ ["--c" as string]: color ?? "var(--accent)" }}>
      <div className="place">{row.pos === 1 ? "1er" : `${row.pos}e`}</div>
      <div className="name">
        <Link href={`/pilote/${row.driver_id}`}>{row.driver}</Link>
      </div>
      <div className="team">{row.team}</div>
      <div className="time">{row.pos === 1 ? fmtLapTime(row.time) : raceGap(row, winner)}</div>
    </div>
  );
}

function MiniDrivers({ standings }: { standings: Standings }) {
  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">Pilotes</span>
      </div>
      <ol className="mini-standings">
        {standings.drivers.slice(0, 5).map((d) => (
          <li key={d.driver_id}>
            <span className="p">{d.pos}</span>
            <TeamSwatch color={d.color} />
            <span className="n">
              <Link href={`/pilote/${d.driver_id}`}>{d.driver}</Link>
              <small>{d.team}</small>
            </span>
            <span className="pts">
              {d.points}
              <small>{d.points > 1 ? "pts" : "pt"}</small>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function MiniTeams({ standings }: { standings: Standings }) {
  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">Écuries</span>
      </div>
      <ol className="mini-standings">
        {standings.teams.slice(0, 5).map((t) => (
          <li key={t.team_id}>
            <span className="p">{t.pos}</span>
            <TeamSwatch color={t.color} />
            <span className="n">
              <Link href={`/ecurie/${t.team_id}`}>{t.team}</Link>
              {t.penalties ? <small>pénalité −{t.penalties}</small> : null}
            </span>
            <span className="pts">
              {t.points}
              <small>{t.points > 1 ? "pts" : "pt"}</small>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
