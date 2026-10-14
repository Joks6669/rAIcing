"use client";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="container">
      <div className="empty" style={{ marginTop: 40 }}>
        <p className="empty-title">Données momentanément indisponibles</p>
        <p>L&apos;API de la ligue ne répond pas. Réessayez dans quelques instants.</p>
        <button type="button" className="btn" onClick={() => reset()}>
          Réessayer
        </button>
      </div>
    </div>
  );
}
