import type { Metadata } from "next";
import MatchView from "@/components/matches/MatchView";

export const metadata: Metadata = { title: "Rencontre" };

export default async function MatchPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <div className="container">
      <MatchView id={id} />
    </div>
  );
}
