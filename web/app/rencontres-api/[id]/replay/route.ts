import { relay } from "../../relay";

export const dynamic = "force-dynamic";

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^[a-z0-9]{8}$/.test(id)) return new Response("rencontre inconnue", { status: 404 });
  const session = new URL(request.url).searchParams.get("session") === "quali" ? "quali" : "race";
  return relay(`/matches/${id}/replay?session=${session}`);
}
