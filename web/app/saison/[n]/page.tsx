import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getSeason, getStandings, orNull } from "@/lib/api";
import { CATEGORY, SEASON_STATUS } from "@/lib/labels";
import { fmtDateShort, fmtInt, fmtKm, fmtMillions, fmtPercent } from "@/lib/format";
import { Pill, RoundStatusPill, SectionTitle, TeamSwatch } from "@/components/ui";
import type { RoundSummary } from "@/lib/types";

type Props = { params: Promise<{ n: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { n } = await params;
  return { title: `Saison ${n}` };
}

export default async function SeasonPage({ params }: Props) {
  const { n: raw } = await params;
  const n = Number.parseInt(raw, 10);
  if (!Number.isFinite(n)) notFound();
  const [season, standings] = await Promise.all([orNull(getSeason(n)), orNull(getStandings(n))]);
  if (!season || !standings) notFound();
  const rounds = [...season.rounds].sort((a, b) => a.number - b.number);
  const done = rounds.filter((r) => r.status === "done").length;
  const next = rounds.find((r) => r.status !== "done");

  return (
    <div className="container">
      <header className="page-head">
        <div className="eyebrow">
          <span>Championnat</span>
          <Pill tone={season.status === "active" ? "accent" : "neutral"}>{SEASON_STATUS[season.status] ?? season.status}</Pill>
        </div>
        <h1>Saison {season.number}</h1>
        <div className="meta-row">
          <span>
            <strong>{done}</strong> / {rounds.length} manches courues
          </span>
          <span>
            Budget par écurie : <strong>{fmtInt(season.budget)}</strong> points de construction
          </span>
          <span>
            Quota d&apos;entraînement : <strong>{fmtMillions(season.quota)}</strong> interactions
          </span>
        </div>
        {season.regulation_change ? (
          <p className="note" style={{ marginTop: 16 }}>
            Changement réglementaire de l&apos;intersaison : <strong>{season.regulation_change.label}</strong> (
            {season.regulation_change.percent > 0 ? "+" : ""}
            {season.regulation_change.percent.toLocaleString("fr-FR")} %).{" "}
            <Link href="/reglement">Voir le règlement</Link>
          </p>
        ) : null}
      </header>

      <section>
        <SectionTitle aside="Circuits procéduraux inédits, révélés après la course">Calendrier</SectionTitle>
        <ol className="calendar">
          {rounds.map((r) => (
            <CalendarRow key={r.id} r={r} isNext={r.id === next?.id} />
          ))}
        </ol>
      </section>

      <section className="section">
        <SectionTitle aside="Barème 25-18-15-12-10-8-6-4-2-1">Classement des pilotes</SectionTitle>
        <div className="table-wrap">
          <table className="data matrix">
            <thead>
              <tr>
                <th className="pos">Pos</th>
                <th className="sticky-col">
                  Pilote
                </th>
                {rounds.map((r) => (
                  <th key={r.id} className="rnd" title={r.track_name ?? `Manche ${r.number}`}>
                    M{r.number}
                  </th>
                ))}
                <th className="num">V.</th>
                <th className="num">Pod.</th>
                <th className="num">Ab.</th>
                <th className="num">Points</th>
              </tr>
            </thead>
            <tbody>
              {standings.drivers.map((d) => (
                <tr key={d.driver_id}>
                  <td className="pos">{d.pos}</td>
                  <td className="sticky-col">
                    <span className="who">
                      <TeamSwatch color={d.color} />
                      <span>
                        <Link href={`/pilote/${d.driver_id}`}>{d.driver}</Link>
                        <small> {d.team}</small>
                      </span>
                    </span>
                  </td>
                  {rounds.map((r, i) => (
                    <PtsCell key={r.id} v={d.by_round[i]} />
                  ))}
                  <td className="num">{d.wins}</td>
                  <td className="num">{d.podiums}</td>
                  <td className="num">{d.dnf}</td>
                  <td className="total">{d.points}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small muted" style={{ marginTop: 8 }}>
          M = manche · V. = victoires · Pod. = podiums · Ab. = abandons et disqualifications. Départage : victoires, puis podiums, puis meilleure place.
        </p>
      </section>

      <section className="section">
        <SectionTitle aside="Somme des deux voitures, pénalités déduites">Classement des écuries</SectionTitle>
        <div className="table-wrap">
          <table className="data matrix">
            <thead>
              <tr>
                <th className="pos">Pos</th>
                <th className="sticky-col">
                  Écurie
                </th>
                {rounds.map((r) => (
                  <th key={r.id} className="rnd">
                    M{r.number}
                  </th>
                ))}
                <th className="num">Pénalités</th>
                <th className="num">Points</th>
              </tr>
            </thead>
            <tbody>
              {standings.teams.map((t) => (
                <tr key={t.team_id}>
                  <td className="pos">{t.pos}</td>
                  <td className="sticky-col">
                    <span className="who">
                      <TeamSwatch color={t.color} />
                      <Link href={`/ecurie/${t.team_id}`}>{t.team}</Link>
                    </span>
                  </td>
                  {rounds.map((r, i) => (
                    <PtsCell key={r.id} v={t.by_round[i]} />
                  ))}
                  <td className={t.penalties ? "num st-out" : "num faint"}>{t.penalties ? `−${t.penalties}` : "0"}</td>
                  <td className="total">{t.points}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function PtsCell({ v }: { v: number | null | undefined }) {
  if (v == null) return <td className="pts na">·</td>;
  return <td className={v === 0 ? "pts zero" : "pts"}>{v}</td>;
}

function CalendarRow({ r, isNext }: { r: RoundSummary; isNext: boolean }) {
  const done = r.status === "done";
  return (
    <li className={isNext ? "is-next" : undefined}>
      <div className="cal-num">
        <small>Manche</small>
        {r.number}
      </div>
      <div className="cal-track">
        {done && r.track_name ? (
          <Link href={`/course/${r.id}`}>{r.track_name}</Link>
        ) : (
          <strong className="muted">Circuit secret</strong>
        )}
        <div className="sub">
          {fmtDateShort(r.date)} · {CATEGORY[r.category]} · {done ? "" : "≈\u00a0"}
          {fmtKm(r.track_length)} · {r.laps}{"\u00a0"}tours · pluie {fmtPercent(r.rain_probability)}
        </div>
      </div>
      <div className="cal-who win">
        <div className="lbl">Vainqueur</div>
        <div className="val">
          {r.winner ? (
            <>
              {r.winner.color ? <TeamSwatch color={r.winner.color} /> : null}
              <Link href={`/pilote/${r.winner.driver_id}`}>{r.winner.driver}</Link>
            </>
          ) : (
            <span className="faint">—</span>
          )}
        </div>
      </div>
      <div className="cal-who pole">
        <div className="lbl">Pole position</div>
        <div className="val">
          {r.pole ? <Link href={`/pilote/${r.pole.driver_id}`}>{r.pole.driver}</Link> : <span className="faint">—</span>}
        </div>
      </div>
      <div className="cal-status">
        <RoundStatusPill status={r.status} />
      </div>
    </li>
  );
}
