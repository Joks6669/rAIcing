// Relais navigateur -> API de la ligue pour les rencontres (le navigateur ne connaît que le site).
import { isMock, serverApiUrl } from "@/lib/api";

const OFFLINE = JSON.stringify({
  detail: "Les rencontres nécessitent le serveur de simulation (API) : définissez RAICING_API_URL.",
});

export async function relay(path: string, init?: RequestInit): Promise<Response> {
  if (isMock()) return new Response(OFFLINE, { status: 503, headers: { "Content-Type": "application/json" } });
  try {
    const upstream = await fetch(`${serverApiUrl()}/api${path}`, { ...init, cache: "no-store" });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return new Response(JSON.stringify({ detail: "Serveur de simulation injoignable." }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}
