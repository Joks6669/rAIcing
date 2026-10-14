import { relay } from "./relay";

export const dynamic = "force-dynamic";

export async function GET() {
  return relay("/matches");
}

export async function POST(request: Request) {
  return relay("/matches", { method: "POST", body: await request.text(), headers: { "Content-Type": "application/json" } });
}
