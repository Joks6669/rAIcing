import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { itemInput } from "@/lib/validation";

export const dynamic = "force-dynamic";

export async function GET() {
  const items = await prisma.item.findMany({ orderBy: { createdAt: "desc" } });
  return NextResponse.json(items);
}

export async function POST(request: Request) {
  const parsed = itemInput.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });
  }
  const item = await prisma.item.create({ data: parsed.data });
  return NextResponse.json(item, { status: 201 });
}
