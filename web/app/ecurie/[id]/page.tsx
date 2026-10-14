import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDriver, getSeason, getStandings, getTeam, orNull } from "@/lib/api";
import { PARTS, TECHNOLOGIES } from "@/lib/labels";
import { fmt1, fmtInt, fmtMillions, fmtPercent, fmtPts, ordinal } from "@/lib/format";
import { EmptyState, Gauge, LevelBar, Pill, SectionTitle } from "@/components/ui";

type Props = { params: Promise<{ id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  const team = await orNull(getTeam(id)).catch(() => null);
  return { title: team?.name ?? "Écurie" };
}

export default async function TeamPage({ params }: Props) {
  const { id } = await params;
  const team = await orNull(getTeam(id));
  if (!team) notFound();
  const seasonN = team.garage?.season ?? team.quota?.season ?? 1;
  const [season, standings, drivers] = await Promise.all([
    orNull(getSeason(seasonN)),
    orNull(getStandings(seasonN)),
    Promise.all(team.drivers.map((d) => orNull(getDriver(d.id)))),
  ]);
  const standing = standings?.teams.find((t) => t.team_id === team.id);
  const g = team.garage;
  const rounds = [...(season?.rounds ?? [])].sort((a, b) => a.number - b.number);

  return (
    <div className="container" style={{ ["--c" as string]: team.color }}>
      <header className="page-head identity">
        <span className="identity-bar" aria-hidden />
        <div>
          <div className="eyebrow">
            <Link href="/ecuries">Écuries</Link>
            {team.is_system ? <Pill>Écurie système</Pill> : null}
          </div>
          <h1>{team.name}</h1>
          <div className="meta-row">
            <span>
              IA directrice : <strong>{team.director}</strong>
            </span>
            {standing ? (
              <span>
                Championnat : <strong>{ordinal(standing.pos)}</strong>, {fmtPts(standing.points)}
              </span>
            ) : null}
            {standing?.penalties ? (
              <span className="st-out">Pénalités : {`−${standing.penalties}`} pts</span>
            ) : null}
          </div>
        </div>
      </header>

      <section>
        <SectionTitle>Pilotes</SectionTitle>
        <div className="driver-cards">
          {team.drivers.map((d, i) => {
            const full = drivers[i];
            return (
              <Link key={d.id} href={`/pilote/${d.id}`} className="driver-card" style={{ ["--c" as string]: team.color }}>
                <span className="num">#{d.number}</span>
                <span>
                  <span className="nm">{d.name}</span>
                  <span className="sub" style={{ display: "block" }}>
                    {d.active_brain ? `Génération ${d.active_brain.generation} active` : "Pilote de référence"}
                  </span>
                </span>
                {full?.rating ? (
                  <span className="rt">
                    <strong>{fmt1(full.rating.overall)}</strong>
                    <span>Driver Rating</span>
                  </span>
                ) : null}
              </Link>
            );
          })}
        </div>
      </section>

      {g ? (
        <section className="section">
          <SectionTitle aside={`Saison ${g.season}`}>Garage</SectionTitle>
          <div className="grid-2">
            <div className="card">
              <div className="card-head">
                <span className="card-title">Pièces</span>
                <span className="small muted">niveaux 0 à 10</span>
              </div>
              <div className="parts">
                {PARTS.map((p) => {
                  const lv = g.parts[p.key] ?? 0;
                  const cap = g.non_compliant?.[p.key];
                  return (
                    <div className="part" key={p.key}>
                      <span className="lbl">
                        {p.label}
                        <small>{cap != null ? `Non conforme : plafonné au niveau ${cap}` : p.effect}</small>
                      </span>
                      <LevelBar value={lv} color={team.color} label={`${p.label} : niveau ${lv} sur 10`} />
                      <span className="lv">{lv}</span>
                    </div>
                  );
                })}
              </div>
              <div style={{ marginTop: 18 }}>
                <div className="card-title" style={{ marginBottom: 8 }}>
                  Technologies
                </div>
                {g.technologies.length ? (
                  <div className="chips">
                    {g.technologies.map((t) => (
                      <span key={t} className="chip">
                        {TECHNOLOGIES[t] ?? t}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="small muted" style={{ margin: 0 }}>
                    Aucune technologie débloquée.
                  </p>
                )}
              </div>
            </div>

            <div className="card">
              <div className="card-head">
                <span className="card-title">Capacité technique</span>
              </div>
              <div className="gauge-row">
                <div className="gr-top">
                  <span>Encombrement</span>
                  <strong>
                    {fmt1(g.capacity.volume[0])} / {fmt1(g.capacity.volume[1])}
                  </strong>
                </div>
                <Gauge value={g.capacity.volume[0]} max={g.capacity.volume[1]} label="Encombrement" />
              </div>
              <div className="gauge-row">
                <div className="gr-top">
                  <span>Charge thermique</span>
                  <strong>
                    {fmt1(g.capacity.thermal[0])} / {fmt1(g.capacity.thermal[1])}
                  </strong>
                </div>
                <Gauge value={g.capacity.thermal[0]} max={g.capacity.thermal[1]} label="Charge thermique" />
              </div>
              <p className="small muted" style={{ marginTop: -4 }}>
                Variation de masse : <strong className="tabular" style={{ color: "var(--text)" }}>{g.capacity.mass_delta > 0 ? "+" : ""}{fmt1(g.capacity.mass_delta)} kg</strong>{" "}
                par rapport à la voiture de base. Le châssis et le refroidissement fixent les limites : on ne peut pas tout empiler.
              </p>

              <div className="card-title" style={{ margin: "20px 0 10px" }}>
                Budget de construction
              </div>
              <div className="gauge-row">
                <div className="gr-top">
                  <span>Dépensé</span>
                  <strong>
                    {fmtInt(g.spent)} / {fmtInt(g.budget)} pts
                  </strong>
                </div>
                <Gauge value={g.spent} max={g.budget} label="Budget dépensé" />
                <span className="small muted">Disponible : {fmtInt(g.available)} pts</span>
              </div>

              {team.quota ? (
                <>
                  <div className="card-title" style={{ margin: "20px 0 10px" }}>
                    Entraînement
                  </div>
                  <div className="gauge-row" style={{ marginBottom: 0 }}>
                    <div className="gr-top">
                      <span>Quota d&apos;interactions utilisé</span>
                      <strong>
                        {fmtMillions(team.quota.used)} / {fmtMillions(team.quota.total)}
                      </strong>
                    </div>
                    <Gauge value={team.quota.used} max={team.quota.total} label="Quota d'entraînement utilisé" />
                    <span className="small muted">
                      {fmtPercent(team.quota.total ? team.quota.used / team.quota.total : 0)} du quota de la saison. Une interaction = une
                      décision d&apos;un pilote en simulateur d&apos;entraînement.
                    </span>
                  </div>
                </>
              ) : null}
            </div>
          </div>
        </section>
      ) : null}

      <section className="section">
        <SectionTitle>Résultats par manche</SectionTitle>
        {team.results.length ? (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Manche</th>
                  <th>Circuit</th>
                  {team.drivers.map((d) => (
                    <th key={d.id} className="num">
                      {d.name}
                    </th>
                  ))}
                  <th className="num">Points</th>
                </tr>
              </thead>
              <tbody>
                {rounds.map((r) => {
                  const res = team.results.find((x) => x.round_id === r.id || x.round === r.number);
                  return (
                    <tr key={r.id}>
                      <td className="muted">M{r.number}</td>
                      <td>{r.status === "done" ? <Link href={`/course/${r.id}`}>{r.track_name ?? "—"}</Link> : <span className="faint">À courir</span>}</td>
                      {team.drivers.map((d, i) => {
                        const dr = drivers[i]?.results.find((x) => x.round_id === r.id);
                        return (
                          <td key={d.id} className={dr && dr.status !== "finished" ? "num st-out" : "num"}>
                            {dr ? (dr.status === "finished" ? ordinal(dr.pos ?? 0) : dr.status === "dsq" ? "DSQ" : "Ab.") : <span className="faint">·</span>}
                          </td>
                        );
                      })}
                      <td className="num strong">{res ? res.points : <span className="faint">·</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="Aucun résultat pour l'instant" />
        )}
      </section>
    </div>
  );
}
