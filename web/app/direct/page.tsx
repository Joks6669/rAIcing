import type { Metadata } from "next";
import Link from "next/link";
import { getLeague, getLive, getRegulations, getSeason, isMock, orNull, publicApiUrl } from "@/lib/api";
import { CATEGORY, CATEGORY_HINT } from "@/lib/labels";
import { fmtDateLong, fmtKm, fmtPercent } from "@/lib/format";
import { EmptyState, RoundStatusPill } from "@/components/ui";
import LiveViewer from "@/components/LiveViewer";

export const metadata: Metadata = { title: "Direct" };

export default async function DirectPage() {
  const [live, league] = await Promise.all([getLive(), getLeague()]);
  const season = await orNull(getSeason(league.current_season));
  const regs = await orNull(getRegulations(league.current_season));

  if (!live.live) {
    const next = season?.rounds.find((r) => r.status !== "done");
    return (
      <div className="container">
        <header className="page-head">
          <div className="eyebrow">
            <span className="live-dot" aria-hidden /> Direct
          </div>
          <h1>Aucune course en direct</h1>
          <p className="lede">
            Les courses sont diffusées ici en temps réel : positions, écarts, arrêts aux stands et incidents, image par image. Aucune
            intervention n&apos;est possible entre le départ et l&apos;arrivée, ni pour les humains ni pour les IA directrices.
          </p>
        </header>
        {next ? (
          <div className="card next-round" style={{ maxWidth: 640 }}>
            <div className="card-head">
              <span className="card-title">Prochain départ</span>
              <RoundStatusPill status={next.status} />
            </div>
            <h2>Manche {next.number}</h2>
            <p className="muted" style={{ margin: "4px 0 0" }}>
              {fmtDateLong(next.date)}
            </p>
            <dl>
              <div>
                <dt>Catégorie</dt>
                <dd>{CATEGORY[next.category]}</dd>
                <dd className="small muted" style={{ fontWeight: 400 }}>
                  {CATEGORY_HINT[next.category]}
                </dd>
              </div>
              <div>
                <dt>Longueur</dt>
                <dd>environ {fmtKm(next.track_length)}</dd>
              </div>
              <div>
                <dt>Course</dt>
                <dd>{next.laps} tours</dd>
              </div>
              <div>
                <dt>Risque de pluie</dt>
                <dd>{fmtPercent(next.rain_probability)}</dd>
              </div>
            </dl>
            <div className="secret">Circuit inédit : il sera découvert en même temps que les pilotes.</div>
          </div>
        ) : (
          <EmptyState title="Saison terminée">Rendez-vous à la saison suivante.</EmptyState>
        )}
        {season ? (
          <p style={{ marginTop: 24 }}>
            <Link href={`/saison/${season.number}`} className="link-arrow">
              Calendrier et résultats de la saison {season.number}
            </Link>
          </p>
        ) : null}
      </div>
    );
  }

  const round = season?.rounds.find((r) => r.id === live.round_id);
  return (
    <div className="layout-wide">
      <div className="container">
        <header className="page-head">
          <div className="eyebrow">
            <span className="live-dot on" aria-hidden /> En direct · {live.session === "quali" ? "Qualifications" : "Course"}
          </div>
          <h1>Manche {round?.number ?? ""}{round?.track_name ? ` — ${round.track_name}` : ""}</h1>
          {round ? (
            <div className="meta-row">
              <span>
                <strong>{round.laps}</strong> tours
              </span>
              <span>
                Catégorie <strong>{CATEGORY[round.category]}</strong>
              </span>
            </div>
          ) : null}
        </header>
        <LiveViewer
          roundId={live.round_id}
          publicApiUrl={publicApiUrl()}
          demo={isMock()}
          startT={live.t}
          compounds={regs?.regulations.compounds}
        />
      </div>
    </div>
  );
}
