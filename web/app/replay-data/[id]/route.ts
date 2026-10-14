// Relais des replays vers le navigateur : fixture gzip en mode maquette, API de la ligue sinon.
import { getReplayResponse } from "@/lib/api";

export const dynamic = "force-dynamic";

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const roundId = Number.parseInt(id, 10);
  if (!Number.isFinite(roundId) || roundId <= 0) return new Response("Manche invalide", { status: 400 });
  const session = new URL(request.url).searchParams.get("session") === "quali" ? "quali" : "race";
  try {
    return await getReplayResponse(roundId, session);
  } catch {
    return new Response("Replay indisponible", { status: 502 });
  }
}
