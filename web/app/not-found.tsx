import Link from "next/link";

export default function NotFound() {
  return (
    <div className="container">
      <div className="empty" style={{ marginTop: 40 }}>
        <p className="empty-title">Page introuvable</p>
        <p>Cette page n&apos;existe pas, ou la donnée demandée n&apos;a pas encore été publiée.</p>
        <p>
          <Link href="/">Retour à l&apos;accueil</Link>
        </p>
      </div>
    </div>
  );
}
