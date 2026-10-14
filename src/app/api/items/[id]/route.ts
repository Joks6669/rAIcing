import { NextResponse } from "next/server";
import { Prisma } from "@prisma/client";
import { prisma } from "@/lib/db";
import { itemUpdate } from "@/lib/validation";

type Params = { params: Promise<{ id: string }> };

function isNotFound(error: unknown) {
  return error instanceof Prisma.PrismaClientKnownRequestError && error.code === "P2025";
}

export async function GET(_request: Request, { params }: Params) {
  const { id } = await params;
  const item = await prisma.item.findUnique({ where: { id } });
  if (!item) return NextResponse.json({ error: "Not found" }, { status: 404 });
  return NextResponse.json(item);
}

export async function PATCH(request: Request, { params }: Params) {
  const { id } = await params;
  const parsed = itemUpdate.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });
  }
  try {
    const item = await prisma.item.update({ where: { id }, data: parsed.data });
    return NextResponse.json(item);
  } catch (error) {
    if (isNotFound(error)) return NextResponse.json({ error: "Not found" }, { status: 404 });
    throw error;
  }
}

export async function DELETE(_request: Request, { params }: Params) {
  const { id } = await params;
  try {
    await prisma.item.delete({ where: { id } });
    return new NextResponse(null, { status: 204 });
  } catch (error) {
    if (isNotFound(error)) return NextResponse.json({ error: "Not found" }, { status: 404 });
    throw error;
  }
}
