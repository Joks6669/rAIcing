import type { Metadata } from "next";
import { getLeague, getRegulations, getSeason, orNull } from "@/lib/api";
import { COMPOUND_NAME } from "@/lib/labels";
import { fmtInt, fmtMillions } from "@/lib/format";
import { CompoundBadge, EmptyState, SectionTitle } from "@/components/ui";
import type { RegulationChange, RegulationsData } from "@/lib/types";

export const metadata: Metadata = { title: "Règlement" };

const nf = (v: number, d = 0) => v.toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });

function num(r: RegulationsData, section: keyof RegulationsData, key: string, fallback: number): number {
  const s = r[section] as Record<string, unknown> | undefined;
  const v = s?.[key];
  return typeof v === "number" ? v : fallback;
}

export default async function RegulationsPage() {
  const league = await getLeague();
  const n = league.current_season;
  const [regs, season] = await Promise.all([orNull(getRegulations(n)), orNull(getSeason(n))]);
  if (!regs) {
    return (
      <div className="container">
        <h1>Règlement</h1>
        <EmptyState title="Règlement indisponible" />
      </div>
    );
  }
  const r = regs.regulations;
  const points = r.race.points ?? [25, 18, 15, 12, 10, 8, 6, 4, 2, 1];
  const pitLane = num(r, "race", "pit_lane_time", 15);
  const service = num(r, "race", "pit_service_time", 3);
  const repair = num(r, "race", "pit_repair_time", 6);
  const repairTh = num(r, "race", "pit_repair_threshold", 0.15);
  const exitSpeed = num(r, "race", "pit_exit_speed", 22);
  const contactPen = num(r, "race", "contact_time_penalty", 5);
  const elimWin = num(r, "race", "elimination_window", 3);
  const teamPen = num(r, "race", "team_penalty_points", 15);
  const minClosing = num(r, "race", "penalty_min_closing", 3);
  const medWear = r.compounds.medium?.wear_rate ?? 0.096;
  const order = ["soft", "medium", "hard", "wet"].filter((c) => r.compounds[c]);
  const changes: RegulationChange[] = regs.changes ?? [];

  return (
    <div className="container">
      <header className="page-head">
        <div className="eyebrow">
          Saison {regs.season} · version {regs.version}
        </div>
        <h1>Règlement</h1>
        <p className="lede">
          Le règlement s&apos;applique automatiquement : le simulateur et les commissaires automatiques le font respecter, sans
          arbitrage humain. Les valeurs ci-dessous sont celles du règlement en vigueur.
        </p>
        <nav className="toc" aria-label="Sommaire">
          <a href="#format">Format</a>
          <a href="#points">Points</a>
          <a href="#stands">Arrêts aux stands</a>
          <a href="#pneus">Pneumatiques</a>
          <a href="#contacts">Contacts</a>
          <a href="#abandons">Abandons</a>
          <a href="#technique">Technique et économie</a>
          <a href="#entrainement">Entraînement</a>
          <a href="#historique">Historique</a>
        </nav>
      </header>

      <section className="section" id="format">
        <SectionTitle>Format sportif</SectionTitle>
        <div className="table-wrap">
          <table className="data">
            <tbody>
              {[
                ["Saison", "10 manches sur des circuits procéduraux inédits, révélés après la course"],
                ["Écurie", "2 voitures et 2 pilotes distincts (deux cerveaux avec leurs propres paramètres)"],
                ["Direction", "Une IA directrice par écurie ; aucune intervention entre le départ et l'arrivée"],
                ["Qualifications", "Un tour de lancement puis un seul tour chronométré, chaque voiture seule en piste, conditions identiques"],
                ["Course", `${num(r, "race", "min_laps", 10)} à ${num(r, "race", "max_laps", 15)} tours selon la longueur du circuit (durée cible d'environ ${nf(num(r, "race", "target_race_time", 1050) / 60)} minutes)`],
                ["Grille", "Ordre des qualifications ; voitures sans temps en fond de grille, dans l'ordre du championnat"],
                ["Départ", "Arrêté, grille en quinconce, feux éteints à t = 0"],
                ["Carburant, voiture de sécurité", "Aucun"],
                ["Fin de course", "Quand le leader boucle le dernier tour, les autres terminent leur tour en cours, dans un délai maximal"],
              ].map(([k, v]) => (
                <tr key={k}>
                  <th scope="row" style={{ width: "28%", whiteSpace: "normal" }}>
                    {k}
                  </th>
                  <td style={{ whiteSpace: "normal" }}>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="section" id="points">
        <SectionTitle aside="Écurie = somme de ses deux voitures">Barème des points</SectionTitle>
        <div className="points-scale">
          {points.map((p, i) => (
            <div key={i}>
              <div className="ps-pos">{i === 0 ? "1er" : `${i + 1}e`}</div>
              <div className="ps-pts">{p}</div>
            </div>
          ))}
        </div>
      </section>

      <div className="rules-grid section">
        <section className="card" id="stands">
          <div className="card-head">
            <span className="card-title">Arrêts aux stands</span>
          </div>
          <p className="small muted">
            Aucun arrêt obligatoire, nombre illimité, changement de gomme libre. Le pilote demande l&apos;arrêt et choisit sa gomme ; il
            est retiré de la piste pendant toute la durée du passage, puis ressort côté droit après la ligne.
          </p>
          <dl className="kv">
            <dt>Entrée des stands</dt>
            <dd>{nf(num(r, "race", "pit_entry_before_line", 50))} m avant la ligne</dd>
            <dt>Sortie des stands</dt>
            <dd>{nf(num(r, "race", "pit_exit_after_line", 150))} m après la ligne</dd>
            <dt>Traversée de la voie des stands</dt>
            <dd>{nf(pitLane, pitLane % 1 ? 1 : 0)} s</dd>
            <dt>Changement de pneus</dt>
            <dd>{nf(service, service % 1 ? 1 : 0)} s</dd>
            <dt>Temps perdu par arrêt</dt>
            <dd>{nf(pitLane + service, (pitLane + service) % 1 ? 1 : 0)} s</dd>
            <dt>Réparation (dégâts &gt; {nf(repairTh * 100)} %)</dt>
            <dd>+{nf(repair)} s</dd>
            <dt>Vitesse en sortie</dt>
            <dd>{nf(exitSpeed * 3.6)} km/h</dd>
            <dt>Sans collision après la sortie</dt>
            <dd>{nf(num(r, "race", "pit_ghost_time", 2))} s</dd>
            <dt>Température des pneus neufs</dt>
            <dd>{nf(num(r, "race", "pit_tyre_temp", 80))} °C</dd>
          </dl>
        </section>

        <section className="card" id="contacts">
          <div className="card-head">
            <span className="card-title">Contacts entre voitures</span>
          </div>
          <p className="small">
            Les collisions sont physiques. À chaque contact, les commissaires automatiques désignent un fautif :
          </p>
          <ul className="small" style={{ paddingLeft: 18, marginTop: 0 }}>
            <li>
              <strong>Par l&apos;arrière</strong> (écart longitudinal &gt; {nf(num(r, "race", "rear_end_gap", 2.5), 1)} m) : la voiture qui
              arrive par l&apos;arrière est fautive, sauf <strong>freinage injustifié</strong> de la voiture de devant dans la seconde
              précédente.
            </li>
            <li>
              <strong>Côte à côte</strong> : la voiture qui se déplaçait latéralement vers l&apos;autre est fautive ; si les deux
              mouvements sont comparables, c&apos;est un incident de course.
            </li>
          </ul>
          <dl className="kv">
            <dt>Contact fautif</dt>
            <dd>+{nf(contactPen)} s</dd>
            <dt>Victime éliminée dans les {nf(elimWin)} s</dt>
            <dd>Disqualification</dd>
            <dt>… et pour son écurie</dt>
            <dd className="st-out">{`−${teamPen}`} points</dd>
            <dt>Seuil de sanction (vitesse de rapprochement)</dt>
            <dd>{nf(minClosing)} m/s</dd>
          </dl>
          <p className="small muted" style={{ marginTop: 12, marginBottom: 0 }}>
            Les sanctions s&apos;appliquent aussi entre coéquipiers : sacrifier une voiture pour éliminer un rival ne rapporte jamais.
          </p>
        </section>
      </div>

      <section className="section" id="pneus">
        <SectionTitle aside="Hors fenêtre de température, l'adhérence chute ; en surchauffe, l'usure double">Pneumatiques</SectionTitle>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Gomme</th>
                <th className="num">Adhérence</th>
                <th className="num">Fenêtre de température</th>
                <th className="num">Usure (medium = 1)</th>
                <th className="num">Perte sur piste mouillée</th>
                <th>Remarque</th>
              </tr>
            </thead>
            <tbody>
              {order.map((c) => {
                const s = r.compounds[c];
                return (
                  <tr key={c}>
                    <td>
                      <span className="who">
                        <CompoundBadge compound={c} size="sm" /> {COMPOUND_NAME[c] ?? c}
                      </span>
                    </td>
                    <td className="num">{nf(s.grip * 100, 1)} %</td>
                    <td className="num">
                      {nf(s.t_opt - s.t_width)} – {nf(s.t_opt + s.t_width)} °C <span className="muted">(optimum {nf(s.t_opt)})</span>
                    </td>
                    <td className="num">× {nf(s.wear_rate / medWear, 2)}</td>
                    <td className="num">{`−${nf(s.wet_loss * 100)}`} %</td>
                    <td className="muted" style={{ whiteSpace: "normal" }}>
                      {c === "wet" ? `Surchauffe sur le sec (×${nf(s.dry_overheat, 1)})` : c === "soft" ? "La plus rapide, s'use vite" : c === "hard" ? "La plus durable" : "Compromis"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="small muted" style={{ marginTop: 8 }}>
          Adhérence relative à la gomme tendre neuve, à température optimale, sur piste sèche. L&apos;humidité est suivie par segment de
          10 m ; certaines zones sèchent plus lentement. Probabilité de pluie par manche : {nf(num(r, "weather", "rain_probability", 0.3) * 100)} %.
        </p>
      </section>

      <section className="section" id="abandons">
        <SectionTitle>Abandons</SectionTitle>
        <div className="grid-3">
          {[
            ["Contact avec un rebord", "Les bords de piste sont des murs : les toucher élimine la voiture."],
            ["Voiture cassée", "Les chocs accumulent des dégâts ; à 100 %, la voiture est hors course."],
            ["Pneu détruit", "Une usure de 100 % provoque une crevaison et l'abandon."],
            ["Arrêt prolongé", `Voiture immobilisée plus de ${nf(num(r, "race", "stall_time", 20))} s.`],
            ["Demi-tour", "Voiture roulant à contresens pendant plusieurs secondes."],
            ["Élimination provoquée", "Disqualification du fautif et pénalité pour son écurie."],
          ].map(([t, d]) => (
            <div className="card" key={t}>
              <h3>{t}</h3>
              <p className="small muted" style={{ margin: "6px 0 0" }}>
                {d}
              </p>
            </div>
          ))}
        </div>
      </section>

      <div className="rules-grid section">
        <section className="card" id="technique">
          <div className="card-head">
            <span className="card-title">Technique et économie</span>
          </div>
          <dl className="kv">
            <dt>Budget de la saison {regs.season}</dt>
            <dd>{fmtInt(season?.budget ?? num(r, "economy", "first_budget", 1000))} pts</dd>
            <dt>Budget des saisons suivantes</dt>
            <dd>
              {fmtInt(num(r, "economy", "budget_min", 850))} à {fmtInt(num(r, "economy", "budget_max", 1150))} pts
            </dd>
            <dt>Report du budget non dépensé</dt>
            <dd>{nf(num(r, "economy", "carry_over_fraction", 0.2) * 100)} % max.</dd>
            <dt>Pièces</dt>
            <dd>7, niveaux 0 à {num(r, "economy", "max_level", 10)}</dd>
            <dt>Coût du niveau suivant</dt>
            <dd>× {nf(num(r, "economy", "cost_growth", 1.35), 2)}</dd>
          </dl>
          <p className="small muted" style={{ marginTop: 12, marginBottom: 0 }}>
            Moteur, appui, carrosserie basse traînée, châssis, freins, refroidissement, suspension. Rendements décroissants ;
            l&apos;encombrement, la charge thermique et la masse sont limités par le châssis et le refroidissement. Aucun revenu en
            cours de saison : le budget est identique pour toutes les écuries.
          </p>
        </section>

        <section className="card" id="entrainement">
          <div className="card-head">
            <span className="card-title">Entraînement et pilotes</span>
          </div>
          <dl className="kv">
            <dt>Quota par écurie et par saison</dt>
            <dd>{fmtMillions(season?.quota ?? num(r, "training", "season_quota", 40_000_000))} interactions</dd>
            <dt>Entraînements simultanés</dt>
            <dd>{num(r, "training", "max_concurrent_jobs", 2)}</dd>
            <dt>Taille maximale d&apos;un cerveau</dt>
            <dd>{fmtMillions(num(r, "training", "max_brain_params", 2_000_000))} paramètres</dd>
            <dt>Nombre maximal de couches</dt>
            <dd>{num(r, "training", "max_brain_layers", 48)}</dd>
            <dt>Cerveaux importés</dt>
            <dd>{r.training.allow_external_brains ? "autorisés" : "interdits"}</dd>
            <dt>Décisions des pilotes</dt>
            <dd>20 par seconde</dd>
          </dl>
          <p className="small muted" style={{ marginTop: 12, marginBottom: 0 }}>
            Une interaction est une décision d&apos;un pilote dans un simulateur d&apos;entraînement, quel que soit l&apos;algorithme.
            Circuits d&apos;entraînement, de validation et de championnat sont distincts.
          </p>
        </section>
      </div>

      <section className="section" id="historique">
        <SectionTitle aside="Un seul paramètre modifié après chaque saison (5 à 10 %, 15 % au maximum)">
          Historique des changements de règlement
        </SectionTitle>
        {changes.length ? (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Saison</th>
                  <th>Paramètre</th>
                  <th className="num">Avant</th>
                  <th className="num">Après</th>
                  <th className="num">Variation</th>
                  <th>Justification</th>
                </tr>
              </thead>
              <tbody>
                {changes.map((c) => (
                  <tr key={`${c.season}-${c.parameter}`}>
                    <td className="strong">S{c.season}</td>
                    <td>
                      {c.label}
                      <br />
                      <code className="small muted">{c.parameter}</code>
                    </td>
                    <td className="num">{c.old.toLocaleString("fr-FR", { maximumFractionDigits: 4 })}</td>
                    <td className="num">{c.new.toLocaleString("fr-FR", { maximumFractionDigits: 4 })}</td>
                    <td className="num strong">
                      {c.percent > 0 ? "+" : ""}
                      {nf(c.percent, 1)} %
                    </td>
                    <td className="muted" style={{ whiteSpace: "normal", minWidth: 220 }}>
                      {c.rationale ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title={`Règlement initial (${regs.version})`}>
            Aucun changement pour l&apos;instant. À l&apos;issue de chaque saison, un paramètre est modifié puis validé sur un banc
            historique (anciens champions et pilotes de référence) : le changement est rejeté s&apos;il détruit trop brutalement leurs
            performances.
          </EmptyState>
        )}
      </section>
    </div>
  );
}
