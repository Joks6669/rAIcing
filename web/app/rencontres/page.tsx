import type { Metadata } from "next";
import MatchesHome from "@/components/matches/MatchesHome";
import { isMock } from "@/lib/api";

export const metadata: Metadata = { title: "Rencontres" };

export default function MatchesPage() {
  return (
    <div className="container">
      <header className="page-head">
        <div className="eyebrow">Courses d&apos;exhibition</div>
        <h1>Rencontres</h1>
        <p className="lede">
          Déposez les modèles de vos pilotes, entraînés où vous voulez, et faites-les courir les uns contre les autres sur un circuit
          inédit. Qualifications, course, replay : tout est simulé ici, sans intervention pendant la course.
        </p>
      </header>
      <MatchesHome offline={isMock()} />
    </div>
  );
}
