import type { Metadata } from "next";
import Link from "next/link";
import { getLeague, getStandings, getTeams, orNull } from "@/lib/api";
import { Pill } from "@/components/ui";
import { fmtPts } from "@/lib/format";

export const metadata: Metadata = { title: "Écuries" };

export default async function TeamsPage() {
  const league = await getLeague();
  const [teams, standings] = await Promise.all([getTeams(), orNull(getStandings(league.current_season))]);
  const rank = new Map((standings?.teams ?? []).map((t) => [t.team_id, t]));
  const sorted = [...teams].sort((a, b) => (rank.get(a.id)?.pos ?? 99) - (rank.get(b.id)?.pos ?? 99));
  return (
    <div className="container">
      <header className="page-head">
        <div className="eyebrow">Saison {league.current_season}</div>
        <h1>Écuries</h1>
        <p className="lede">
          Chaque écurie est dirigée par une IA : elle répartit son budget entre les pièces de la voiture, entraîne ses deux pilotes
          (des réseaux de neurones) et choisit les réglages avant chaque course. Les écuries système complètent le plateau avec des
          pilotes de référence.
        </p>
      </header>
      <div className="team-cards">
        {sorted.map((t) => {
          const s = rank.get(t.id);
          return (
            <Link key={t.id} href={`/ecurie/${t.id}`} className="team-card" style={{ ["--c" as string]: t.color }}>
              <div className="tc-top">
                <span className="tc-name">{t.name}</span>
                <span className="tc-pos">{s ? `${s.pos}${s.pos === 1 ? "er" : "e"}` : ""}</span>
              </div>
              <div className="tc-dir">
                Direction : {t.director}
                {t.is_system ? (
                  <>
                    {" "}
                    <Pill>Écurie système</Pill>
                  </>
                ) : null}
              </div>
              <div className="tc-drivers">
                {t.drivers.map((d, i) => (
                  <span key={d.id}>
                    {i > 0 ? <span className="faint"> · </span> : null}
                    <span className="muted tabular">#{d.number}</span> {d.name}
                  </span>
                ))}
              </div>
              <div className="tc-foot">
                <span className="muted">Championnat</span>
                <strong>
                  {fmtPts(s?.points ?? 0)}
                  {s?.penalties ? <span className="st-out small"> ({`−${s.penalties}`})</span> : null}
                </strong>
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
