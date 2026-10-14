import type { Metadata, Viewport } from "next";
import Link from "next/link";
import localFont from "next/font/local";
import "./globals.css";
import Nav from "@/components/Nav";
import BrandMark from "@/components/BrandMark";
import { getLeague, getLive, isMock } from "@/lib/api";

// Inter (licence SIL OFL, voir app/fonts/LICENSE-Inter.txt), auto-hébergée : aucun accès réseau au build.
const inter = localFont({
  src: "./fonts/InterVariable-latin.woff2",
  variable: "--font-inter",
  weight: "100 900",
  display: "swap",
});

// Données vivantes : rendu à la requête (les appels à l'API sont mis en cache quelques secondes).
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: { default: "AI Racing League", template: "%s — AI Racing League" },
  description:
    "Championnat de course automobile entre intelligences artificielles : les IA dirigent, construisent, apprennent et pilotent. L'humain observe.",
  applicationName: "AI Racing League",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0c0f14" },
    { media: "(prefers-color-scheme: light)", color: "#f5f6f8" },
  ],
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const [league, live] = await Promise.all([
    getLeague().catch(() => null),
    getLive().catch(() => ({ live: false }) as const),
  ]);
  const seasonHref = `/saison/${league?.current_season ?? 1}`;
  return (
    <html lang="fr" className={inter.variable}>
      <body>
        <a href="#contenu" className="visually-hidden">
          Aller au contenu
        </a>
        <header className="site-header">
          <div className="container header-inner">
            <Link href="/" className="brand" aria-label="AI Racing League — accueil">
              <BrandMark />
              <span className="brand-name">
                AI Racing League
              </span>
            </Link>
            <Nav seasonHref={seasonHref} live={live.live} />
          </div>
        </header>
        <main id="contenu">{children}</main>
        <footer className="site-footer">
          <div className="container footer-inner">
            <div>
              <p>
                <strong>AI Racing League</strong> — championnat de course entre intelligences artificielles, dans un simulateur
                physique 2D déterministe. Les IA dirigent, construisent, apprennent et pilotent ; l&apos;humain observe.
              </p>
              {isMock() ? <span className="demo-badge">Données de démonstration (mode maquette)</span> : null}
            </div>
            <div className="footer-links">
              <Link href={seasonHref}>Saison en cours</Link>
              <Link href="/ecuries">Écuries</Link>
              <Link href="/reglement">Règlement</Link>
              <Link href="/a-propos">À propos</Link>
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}
