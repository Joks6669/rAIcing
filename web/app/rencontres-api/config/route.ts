import { relay } from "../relay";

export const dynamic = "force-dynamic";

export async function GET() {
  return relay("/matches/config");
}
