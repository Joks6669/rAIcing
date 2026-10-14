import { relay } from "../relay";

export const dynamic = "force-dynamic";

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^[a-z0-9]{8}$/.test(id)) return new Response(JSON.stringify({ detail: "rencontre inconnue" }), { status: 404 });
  return relay(`/matches/${id}`);
}
