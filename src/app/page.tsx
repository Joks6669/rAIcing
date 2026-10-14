export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-6 p-8">
      <h1 className="text-4xl font-semibold tracking-tight">rAIcing</h1>
      <p className="text-lg opacity-80">Le projet est en place. Points d&apos;entrée de l&apos;API :</p>
      <ul className="list-inside list-disc font-mono text-sm">
        <li>GET /api/health</li>
        <li>GET, POST /api/items</li>
        <li>GET, PATCH, DELETE /api/items/:id</li>
      </ul>
    </main>
  );
}
