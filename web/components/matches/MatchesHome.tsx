"use client";
// Page « Rencontres » : formulaire de création (modèles déposés + pilotes de référence) et liste.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { fmtDateShort, fmtKm } from "@/lib/format";
import type { MatchMeta } from "./types";

const COLORS = ["#E10600", "#00A3E0", "#FFC906", "#7CD321", "#B14CFF", "#FF7A00", "#00D2BE", "#FF4FA3", "#4A6CFF", "#F2F2F2"];
const REFERENCES: [string, string][] = [
  ["pro", "Référence (pro)"],
  ["rapide", "Rapide mais risqué"],
  ["pneus", "Spécialiste des pneus"],
  ["cooperatif", "Coopératif"],
  ["prudent", "Prudent"],
  ["debutant", "Débutant"],
];
const STARTS: [string, string][] = [
  ["auto", "Gomme auto"],
  ["soft", "Tendres"],
  ["medium", "Mediums"],
  ["hard", "Durs"],
  ["wet", "Pluie"],
];
const STATUS: Record<string, string> = { queued: "En attente", running: "En cours", done: "Terminée", failed: "Échec" };

interface Row {
  key: number;
  kind: "brain" | "reference";
  name: string;
  color: string;
  start: string;
  reference: string;
  file: File | null;
}

let nextKey = 1;
function newRow(kind: Row["kind"], i: number): Row {
  return {
    key: nextKey++,
    kind,
    name: kind === "brain" ? "" : "",
    color: COLORS[i % COLORS.length],
    start: "auto",
    reference: "pro",
    file: null,
  };
}

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] ?? "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

export default function MatchesHome({ offline }: { offline: boolean }) {
  const router = useRouter();
  const [rows, setRows] = useState<Row[]>(() => [newRow("brain", 0), newRow("brain", 1)]);
  const [name, setName] = useState("");
  const [trackMode, setTrackMode] = useState<"random" | "seed">("random");
  const [seed, setSeed] = useState("");
  const [laps, setLaps] = useState("");
  const [weather, setWeather] = useState("dry");
  const [fill, setFill] = useState(4);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [list, setList] = useState<MatchMeta[] | null>(null);
  const [needPassword, setNeedPassword] = useState(false);
  const [password, setPassword] = useState("");

  useEffect(() => {
    if (offline) return;
    fetch("/rencontres-api/config", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((c) => c && setNeedPassword(!!c.password_required))
      .catch(() => undefined);
  }, [offline]);

  useEffect(() => {
    if (offline) return;
    let stop = false;
    const load = () =>
      fetch("/rencontres-api", { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : []))
        .then((d: MatchMeta[]) => !stop && setList(d))
        .catch(() => !stop && setList([]));
    load();
    const id = setInterval(load, 5000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [offline]);

  const update = (key: number, patch: Partial<Row>) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const total = rows.length + fill;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const missing = rows.find((r) => r.kind === "brain" && !r.file);
    if (missing) {
      setError(`Choisissez le fichier du modèle de « ${missing.name || "participant sans nom"} ».`);
      return;
    }
    if (total < 1) {
      setError("La grille est vide.");
      return;
    }
    setBusy(true);
    try {
      const entrants = await Promise.all(
        rows.map(async (r, i) => ({
          name: r.name.trim() || (r.kind === "brain" ? r.file?.name.replace(/\.zip$/i, "") ?? `Pilote ${i + 1}` : ""),
          color: r.color,
          start: r.start,
          ...(r.kind === "brain" ? { package: await toBase64(r.file!) } : { reference: r.reference }),
        })),
      );
      const body = {
        password: needPassword ? password : undefined,
        name,
        entrants,
        fill,
        weather,
        track_seed: trackMode === "seed" && seed.trim() ? seed.trim() : null,
        laps: laps.trim() ? Number(laps) : null,
      };
      const res = await fetch("/rencontres-api", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const d = data?.detail;
        setError(typeof d === "string" ? d : Array.isArray(d) ? d.map((x: { msg?: string }) => x.msg).join(" ; ") : `Erreur ${res.status}`);
        return;
      }
      router.push(`/rencontres/${data.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur d'envoi");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="matches">
      {offline ? (
        <div className="note" role="status">
          Le site tourne en mode démonstration : les rencontres ont besoin du serveur de simulation. Lancez l&apos;API
          (<code>python -m raicing.api</code>) puis le site avec <code>RAICING_API_URL</code> (voir <code>docs/RENCONTRES.md</code>).
        </div>
      ) : null}

      <form className="card match-form" onSubmit={submit}>
        <div className="card-head">
          <span className="card-title">Nouvelle rencontre</span>
          <span className="small muted">{total} voiture{total > 1 ? "s" : ""} sur la grille</span>
        </div>

        <label className="field">
          <span>Nom de la rencontre</span>
          <input type="text" value={name} maxLength={80} placeholder="ex. Astra contre Opus, manche d'essai" onChange={(e) => setName(e.target.value)} />
        </label>

        <div className="entrants">
          {rows.map((r, i) => (
            <div key={r.key} className="entrant" style={{ ["--c" as string]: r.color }}>
              <span className="entrant-num">{i + 1}</span>
              <input type="color" value={r.color} aria-label="Couleur" onChange={(e) => update(r.key, { color: e.target.value })} />
              <input
                type="text"
                className="entrant-name"
                value={r.name}
                maxLength={40}
                placeholder={r.kind === "brain" ? "Nom du pilote" : "Nom (facultatif)"}
                aria-label="Nom du pilote"
                onChange={(e) => update(r.key, { name: e.target.value })}
              />
              {r.kind === "brain" ? (
                <label className={`file-pick ${r.file ? "has-file" : ""}`}>
                  <input type="file" accept=".zip,application/zip" onChange={(e) => update(r.key, { file: e.target.files?.[0] ?? null })} />
                  <span>{r.file ? `${r.file.name} · ${Math.max(1, Math.round(r.file.size / 1024))} Ko` : "Choisir le modèle (.zip)"}</span>
                </label>
              ) : (
                <select value={r.reference} aria-label="Pilote de référence" onChange={(e) => update(r.key, { reference: e.target.value })}>
                  {REFERENCES.map(([k, l]) => (
                    <option key={k} value={k}>
                      {l}
                    </option>
                  ))}
                </select>
              )}
              <select value={r.start} aria-label="Gomme de départ" onChange={(e) => update(r.key, { start: e.target.value })}>
                {STARTS.map(([k, l]) => (
                  <option key={k} value={k}>
                    {l}
                  </option>
                ))}
              </select>
              <button type="button" className="btn btn-ghost entrant-del" aria-label="Retirer" onClick={() => setRows((rs) => rs.filter((x) => x.key !== r.key))}>
                ✕
              </button>
            </div>
          ))}
        </div>
        <div className="entrant-add">
          <button type="button" className="btn" onClick={() => setRows((rs) => [...rs, newRow("brain", rs.length)])} disabled={rows.length >= 20}>
            + Ajouter un modèle
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => setRows((rs) => [...rs, newRow("reference", rs.length)])} disabled={rows.length >= 20}>
            + Ajouter un pilote de référence
          </button>
        </div>

        <div className="match-options">
          <label className="field">
            <span>Circuit</span>
            <select value={trackMode} onChange={(e) => setTrackMode(e.target.value as "random" | "seed")}>
              <option value="random">Inédit (tiré au hasard)</option>
              <option value="seed">Choisir une graine</option>
            </select>
            {trackMode === "seed" ? (
              <input type="number" min={0} value={seed} placeholder="ex. 42" aria-label="Graine du circuit" onChange={(e) => setSeed(e.target.value)} />
            ) : null}
          </label>
          <label className="field">
            <span>Tours</span>
            <input type="number" min={1} max={30} value={laps} placeholder="auto (10 à 15)" onChange={(e) => setLaps(e.target.value)} />
          </label>
          <label className="field">
            <span>Météo</span>
            <select value={weather} onChange={(e) => setWeather(e.target.value)}>
              <option value="dry">Sec</option>
              <option value="rain">Pluie</option>
              <option value="random">Au hasard (30 % de pluie)</option>
            </select>
          </label>
          <label className="field">
            <span>Compléter avec des pilotes de référence</span>
            <input type="number" min={0} max={Math.max(0, 20 - rows.length)} value={fill} onChange={(e) => setFill(Math.max(0, Math.min(20 - rows.length, Number(e.target.value) || 0)))} />
          </label>
        </div>

        {needPassword ? (
          <label className="field match-password">
            <span>Mot de passe pour lancer une rencontre</span>
            <input type="password" value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
          </label>
        ) : null}
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="match-submit">
          <button type="submit" className="btn btn-primary btn-lg" disabled={busy || offline}>
            {busy ? "Envoi…" : "Lancer la rencontre"}
          </button>
          <span className="small muted">
            Format attendu : un <code>.zip</code> avec <code>brain.json</code> et <code>weights.npz</code> (guide : <code>docs/RENCONTRES.md</code>).
          </span>
        </div>
      </form>

      <section className="section">
        <div className="section-title">
          <h2>Dernières rencontres</h2>
        </div>
        {list === null ? (
          <p className="muted">{offline ? "Indisponible en mode démonstration." : "Chargement…"}</p>
        ) : list.length === 0 ? (
          <p className="muted">Aucune rencontre pour l&apos;instant.</p>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Rencontre</th>
                  <th>Circuit</th>
                  <th className="num">Voitures</th>
                  <th>Vainqueur</th>
                  <th>Statut</th>
                </tr>
              </thead>
              <tbody>
                {list.map((m) => {
                  const win = m.results?.race?.[0];
                  return (
                    <tr key={m.id}>
                      <td className="who">
                        <Link href={`/rencontres/${m.id}`}>{m.name}</Link>
                        <small>{fmtDateShort(m.created_at)}</small>
                      </td>
                      <td>
                        {m.track.name} <span className="muted small">· {fmtKm(m.track.length)} · {m.laps} tours</span>
                      </td>
                      <td className="num">{m.entrants.length}</td>
                      <td>{win ? win.driver : "—"}</td>
                      <td>
                        <span className={`pill pill-match-${m.status}`}>{STATUS[m.status] ?? m.status}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
