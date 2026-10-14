"use client";
// Télécharge un replay (JSON, éventuellement gzip), construit le modèle et affiche la visionneuse.
import { useEffect, useState } from "react";
import { ReplayModel } from "@/lib/replay-model";
import type { CompoundSpec, Replay } from "@/lib/types";
import ReplayViewer from "./ReplayViewer";

export async function decodeReplay(res: Response): Promise<Replay> {
  const buf = new Uint8Array(await res.arrayBuffer());
  if (buf[0] === 0x1f && buf[1] === 0x8b) {
    // fichier .json.gz servi sans Content-Encoding : décompression dans le navigateur
    const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
    return JSON.parse(await new Response(stream).text()) as Replay;
  }
  return JSON.parse(new TextDecoder().decode(buf)) as Replay;
}

export function replayUrl(roundId: number, session: "race" | "quali"): string {
  return `/replay-data/${roundId}?session=${session}`;
}

type State = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; model: ReplayModel };

export default function ReplayLoader({
  roundId,
  sessions,
  compounds,
}: {
  roundId: number;
  sessions: { race: boolean; quali: boolean };
  compounds?: Record<string, CompoundSpec>;
}) {
  const [session, setSession] = useState<"race" | "quali">(sessions.race ? "race" : "quali");
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    fetch(replayUrl(roundId, session))
      .then(async (res) => {
        if (!res.ok) throw new Error(res.status === 404 ? "Replay indisponible pour cette séance." : `Erreur ${res.status}`);
        const replay = await decodeReplay(res);
        if (!replay.frames?.length) throw new Error("Replay vide.");
        const { frames, ...header } = replay;
        const model = new ReplayModel(header, frames);
        if (!cancelled) setState({ status: "ready", model });
      })
      .catch((e: unknown) => {
        if (!cancelled) setState({ status: "error", message: e instanceof Error ? e.message : "Erreur de chargement" });
      });
    return () => {
      cancelled = true;
    };
  }, [roundId, session]);

  return (
    <div className="replay-loader">
      {sessions.race && sessions.quali ? (
        <div className="seg seg-tabs" role="tablist" aria-label="Séance">
          <button type="button" role="tab" aria-selected={session === "race"} className={session === "race" ? "is-active" : ""} onClick={() => setSession("race")}>
            Course
          </button>
          <button type="button" role="tab" aria-selected={session === "quali"} className={session === "quali" ? "is-active" : ""} onClick={() => setSession("quali")}>
            Qualifications
          </button>
        </div>
      ) : null}
      {state.status === "ready" ? (
        <ReplayViewer key={session} model={state.model} compounds={compounds} />
      ) : (
        <div className="viewer-placeholder" role="status">
          {state.status === "loading" ? (
            <>
              <span className="spinner" aria-hidden />
              <span>Chargement du replay…</span>
            </>
          ) : (
            <span>{state.message}</span>
          )}
        </div>
      )}
    </div>
  );
}
