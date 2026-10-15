"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const LINKS = [
  { href: "/", label: "Accueil", match: (p: string) => p === "/" },
  { href: "/saison", label: "Saison", match: (p: string) => p.startsWith("/saison") || p.startsWith("/course") },
  { href: "/direct", label: "Direct", match: (p: string) => p.startsWith("/direct") },
  { href: "/rencontres", label: "Rencontres", match: (p: string) => p.startsWith("/rencontres") },
  { href: "/ecuries", label: "Écuries", match: (p: string) => p.startsWith("/ecurie") || p.startsWith("/pilote") },
  { href: "/reglement", label: "Règlement", match: (p: string) => p.startsWith("/reglement") },
  { href: "/a-propos", label: "À propos", match: (p: string) => p.startsWith("/a-propos") },
];

export default function Nav({ seasonHref, live, matchesOnly = false }: { seasonHref: string; live: boolean; matchesOnly?: boolean }) {
  const pathname = usePathname() ?? "/";
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [pathname]);
  return (
    <>
      <button type="button" className="nav-toggle" aria-expanded={open} aria-controls="site-nav" onClick={() => setOpen((o) => !o)}>
        <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden>
          {open ? (
            <path d="M4 4l10 10M14 4L4 14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          ) : (
            <path d="M2 5h14M2 9h14M2 13h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          )}
        </svg>
        <span className="visually-hidden">Menu</span>
      </button>
      <nav id="site-nav" className={open ? "nav open" : "nav"} aria-label="Navigation principale">
        {LINKS.filter((l) => !matchesOnly || l.href === "/rencontres").map((l) => {
          const href = l.href === "/saison" ? seasonHref : l.href;
          const active = l.match(pathname);
          return (
            <Link key={l.href} href={href} aria-current={active ? "page" : undefined}>
              {l.href === "/direct" ? <span className={live ? "nav-live-dot on" : "nav-live-dot"} aria-hidden /> : null}
              {l.label}
            </Link>
          );
        })}
      </nav>
    </>
  );
}
