// Hébergement « rencontres seules » (RAICING_SITE=rencontres) : le championnat n'existe pas,
// toutes les autres pages renvoient vers /rencontres.
import { NextResponse, type NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  if (process.env.RAICING_SITE !== "rencontres") return NextResponse.next();
  const { pathname } = request.nextUrl;
  if (pathname.startsWith("/rencontres")) return NextResponse.next();
  return NextResponse.redirect(new URL("/rencontres", request.url));
}

export const config = {
  // pages only: assets, fonts and Next internals are left alone
  matcher: ["/((?!_next/|icon|favicon|.*\\.(?:svg|png|jpg|woff2?|ico|txt)$).*)"],
};
