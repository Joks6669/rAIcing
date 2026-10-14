"use client";
// Direct : se connecte au WebSocket /ws/live (docs/API.md) et alimente la visionneuse image par image.
// En mode maquette (démonstration), le replay de la manche est rejoué en temps réel comme un flux.
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ReplayModel } from "@/lib/replay-model";
import type { CompoundSpec, ReplayEvent, ReplayFrame, ReplayHeader } from "@/lib/types";
import ReplayViewer from "./ReplayViewer";
import { decodeReplay, replayUrl } from "./ReplayLoader";

type Conn = "connecting" | "live" | "reconnecting" | "ended" | "error";

const CONN_LABEL: Record<Conn, string> = {
  connecting: "Connexion au direct…",
  live: "Connecté",
  reconnecting: "Connexion perdue, nouvelle tentative…",
  ended: "Course terminée",
  error: "Direct indisponible",
};

function wsUrlFrom(publicApiUrl: string | null): string {
  if (publicApiUrl) return `${publicApiUrl.replace(/^http/, "ws")}/ws/live`;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/live`;
}

export default function LiveViewer({
  roundId,
  publicApiUrl,
  demo,
  startT = 0,
  compounds,
}: {
  roundId: number;
  publicApiUrl: string | null;
  demo: boolean;
  startT?: number;
  compounds?: Record<string, CompoundSpec>;
}) {
  const [model, setModelState] = useState<ReplayModel | null>(null);
  const [modelKey, setModelKey] = useState(0);
  const setModel = (m: ReplayModel) => {
    setModelState(m);
    setModelKey((n) => n + 1);
  };
  const [conn, setConn] = useState<Conn>("connecting");
  const modelRef = useRef<ReplayModel | null>(null);

  // ---------------------------------------------------------- démonstration (maquette)
  useEffect(() => {
    if (!demo) return;
    let timer: ReturnType<typeof setInterval> | null = null;
    let cancelled = false;
    fetch(replayUrl(roundId, "race"))
      .then(decodeReplay)
      .then((replay) => {
        if (cancelled) return;
        const { frames, events = [], ...rest } = replay;
        const header: ReplayHeader = { ...rest, events: [] };
        const m = new ReplayModel(header, [], { live: true });
        let k = 0;
        let e = 0;
        // reprise au temps courant annoncé par /api/live
        while (k < frames.length - 1 && frames[k][0] < startT) m.appendFrame(frames[k++]);
        const pushNext = () => {
          if (k >= frames.length) {
            m.markEnded();
            setConn("ended");
            if (timer) clearInterval(timer);
            return;
          }
          const f = frames[k++];
          while (e < events.length && events[e].t <= f[0]) m.addEvent(events[e++]);
          m.appendFrame(f);
        };
        pushNext();
        modelRef.current = m;
        setModel(m);
        setConn("live");
        timer = setInterval(pushNext, (replay.dt || 0.2) * 1000);
      })
      .catch(() => setConn("error"));
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [demo, roundId, startT]);

  // ---------------------------------------------------------- WebSocket
  useEffect(() => {
    if (demo) return;
    let ws: WebSocket | null = null;
    let closed = false;
    let retry = 0;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      setConn(retry === 0 ? "connecting" : "reconnecting");
      try {
        ws = new WebSocket(wsUrlFrom(publicApiUrl));
      } catch {
        setConn("error");
        return;
      }
      ws.onopen = () => {
        retry = 0;
      };
      ws.onmessage = (msg) => {
        let data: { type: string; replay?: ReplayHeader & { frames?: ReplayFrame[] }; frame?: ReplayFrame; event?: ReplayEvent; data?: ReplayEvent };
        try {
          data = JSON.parse(typeof msg.data === "string" ? msg.data : "");
        } catch {
          return;
        }
        if (data.type === "init" && data.replay) {
          const { frames = [], ...header } = data.replay;
          const m = new ReplayModel(header, frames, { live: true });
          modelRef.current = m;
          setModel(m);
          setConn("live");
        } else if (data.type === "frame" && data.frame) {
          modelRef.current?.appendFrame(data.frame);
        } else if (data.type === "event") {
          const ev = data.event ?? data.data;
          if (ev) modelRef.current?.addEvent(ev);
        } else if (data.type === "end") {
          closed = true;
          modelRef.current?.markEnded();
          setConn("ended");
          ws?.close();
        }
      };
      ws.onclose = () => {
        if (closed) return;
        retry += 1;
        setConn("reconnecting");
        retryTimer = setTimeout(connect, Math.min(15000, 1000 * 2 ** Math.min(retry, 4)));
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      ws?.close();
    };
  }, [demo, publicApiUrl]);

  return (
    <div className="live">
      <div className="live-status">
        <span className={`live-dot ${conn === "live" ? "on" : ""}`} aria-hidden />
        <span>{CONN_LABEL[conn]}</span>
        {demo ? <span className="muted">· démonstration à partir du replay de la manche {roundId}</span> : null}
        {conn === "ended" ? (
          <Link href={`/course/${roundId}`} className="live-link">
            Voir les résultats
          </Link>
        ) : null}
      </div>
      {model ? (
        <ReplayViewer key={modelKey} model={model} live compounds={compounds} />
      ) : (
        <div className="viewer-placeholder" role="status">
          {conn === "error" ? <span>Le flux du direct est indisponible pour le moment.</span> : (
            <>
              <span className="spinner" aria-hidden />
              <span>En attente du flux…</span>
            </>
          )}
        </div>
      )}
    </div>
  );
}
