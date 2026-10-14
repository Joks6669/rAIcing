import { redirect } from "next/navigation";
import { getLeague } from "@/lib/api";

export default async function SaisonIndex() {
  const league = await getLeague();
  redirect(`/saison/${league.current_season}`);
}
