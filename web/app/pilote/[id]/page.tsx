import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDriver, getSeason, getStandings, orNull } from "@/lib/api";
import { METHODS, RATING_COMPONENTS, raceStatusLabel } from "@/lib/labels";
import { fmt1, fmtDate, fmtInt, fmtMillions, fmtPts, ordinal } from "@/lib/format";
import { EmptyState, SectionTitle } from "@/components/ui";

type Props = { params: Promise<{ id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  const d = await orNull(getDriver(id)).catch(() => null);
  return { title: d ? `${d.name} (${d.team})` : "Pilote" };
}

export default async function DriverPage({ params }: Props) {
  const { id } = await params;
  const driver = await orNull(getDriver(id));
  if (!driver) notFound();
  const seasonN = driver.results[0]?.season ?? 1;
  const [season, standings] = await Promise.all([orNull(getSeason(seasonN)), orNull(getStandings(seasonN))]);
  const st = standings?.drivers.find((d) => d.driver_id === driver.id);
  const rating = driver.rating;
  const brain = driver.active_brain;
  const roundName = (rid: number) => season?.rounds.find((r) => r.id === rid)?.track_name ?? `Manche`;

  // échelle de l'intervalle : 0-100, référence = 50
  const pct = (v: number) => `${Math.max(0, Math.min(100, v))}%`;

  return (
    <div className="container" style={{ ["--c" as string]: driver.color }}>
      <header className="page-head identity">
        <span className="number-badge">#{driver.number}</span>
        <div>
          <div className="eyebrow">
            <Link href={`/ecurie/${driver.team_id}`}>{driver.team}</Link>
            <span>· Pilote</span>
          </div>
          <h1>{driver.name}</h1>
          <div className="meta-row">
            {brain ? (
              <span>
                Cerveau actif : <strong>génération {brain.generation}</strong> ({METHODS[brain.method] ?? brain.method},{" "}
                {fmtInt(brain.params)} paramètres)
              </span>
            ) : (
              <span>Pilote de référence (écurie système)</span>
            )}
            {st ? (
              <span>
                Championnat : <strong>{ordinal(st.pos)}</strong>, {fmtPts(st.points)}
              </span>
            ) : null}
          </div>
        </div>
      </header>

      <section className="card">
        <div className="card-head">
          <span className="card-title">Driver Rating</span>
          <span className="small muted">Évaluation standardisée sur circuits de validation</span>
        </div>
        {rating ? (
          <div className="rating">
            <div>
              <div className="rating-score">
                {fmt1(rating.overall)}
                <small>/ 100</small>
              </div>
              <div className="ci-band" aria-label={`Intervalle d'incertitude de ${fmt1(rating.ci[0])} à ${fmt1(rating.ci[1])}`}>
                <span className="axis" />
                <span className="ref" style={{ left: "50%" }} title="Pilote de référence = 50" />
                <span className="range" style={{ left: pct(rating.ci[0]), width: `${Math.max(1, rating.ci[1] - rating.ci[0])}%` }} />
                <span className="point" style={{ left: pct(rating.overall) }} />
              </div>
              <div className="ci-labels">
                <span>0</span>
                <span>référence 50</span>
                <span>100</span>
              </div>
              <p className="small muted" style={{ marginTop: 10 }}>
                Intervalle d&apos;incertitude : {fmt1(rating.ci[0])} – {fmt1(rating.ci[1])}
              </p>
              <p className="note" style={{ marginTop: 14 }}>
                Le Driver Rating est purement informatif : il n&apos;a aucun effet en course. 50 correspond au niveau du pilote de
                référence, 100 à un pilote nettement meilleur.
              </p>
            </div>
            <div className="components">
              {RATING_COMPONENTS.map((c) => {
                const v = rating.components[c.key];
                return (
                  <div className="component" key={c.key}>
                    <span className="lbl">
                      {c.label}
                      <small>{c.hint}</small>
                    </span>
                    <span className="cbar" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={v} aria-label={c.label}>
                      <span style={{ width: pct(v) }} />
                      <i title="Référence : 50" />
                    </span>
                    <span className="val">{fmt1(v)}</span>
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <EmptyState title="Pas encore évalué">Aucun Driver Rating n&apos;a été calculé pour le cerveau actif.</EmptyState>
        )}
      </section>

      <section className="section">
        <SectionTitle aside="Chaque entraînement produit une nouvelle génération">Générations</SectionTitle>
        {driver.generations.length ? (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Génération</th>
                  <th>Méthode</th>
                  <th>Date</th>
                  <th className="num">Rating</th>
                  <th className="num">Interactions d&apos;entraînement</th>
                  <th>Issue de</th>
                </tr>
              </thead>
              <tbody>
                {driver.generations.map((g) => {
                  const parent = g.parent != null ? driver.generations.find((x) => x.id === g.parent) : null;
                  const active = brain?.id === g.id;
                  return (
                    <tr key={g.id}>
                      <td className="strong">
                        {g.generation}
                        {active ? (
                          <span className="pill pill-tone-accent" style={{ marginLeft: 8 }}>
                            active
                          </span>
                        ) : null}
                      </td>
                      <td>{METHODS[g.method] ?? g.method}</td>
                      <td className="muted">{fmtDate(g.created_at)}</td>
                      <td className="num">{g.rating != null ? fmt1(g.rating) : <span className="faint">non évaluée</span>}</td>
                      <td className="num">{fmtMillions(g.training_steps)}</td>
                      <td className="muted">{parent ? `génération ${parent.generation}` : g.parent != null ? `cerveau ${g.parent}` : "nouvelle architecture"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="Aucune génération">
            Ce pilote est un pilote de référence fourni par la ligue : il n&apos;est pas entraîné par une écurie.
          </EmptyState>
        )}
      </section>

      <section className="section">
        <SectionTitle>Résultats en course</SectionTitle>
        {driver.results.length ? (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Manche</th>
                  <th>Circuit</th>
                  <th className="num">Grille</th>
                  <th className="num">Arrivée</th>
                  <th className="num">Points</th>
                  <th>Statut</th>
                </tr>
              </thead>
              <tbody>
                {driver.results.map((r) => (
                  <tr key={r.round_id}>
                    <td className="muted">
                      S{r.season} · M{r.round}
                    </td>
                    <td>
                      <Link href={`/course/${r.round_id}`}>{roundName(r.round_id)}</Link>
                    </td>
                    <td className="num muted">{r.grid ?? "—"}</td>
                    <td className="num strong">{r.status === "finished" && r.pos ? ordinal(r.pos) : "—"}</td>
                    <td className="num strong">{r.points}</td>
                    <td className={r.status === "finished" ? "st-ok" : "st-out"}>{raceStatusLabel(r.status, "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="Aucune course disputée" />
        )}
      </section>
    </div>
  );
}
