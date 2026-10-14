// Description en français des événements d'un replay (fil des événements, frise).
import { COMPOUND_PLURAL, CONTACT_KIND, ELIM_REASON_SHORT } from "./labels";
import type { ReplayModel } from "./replay-model";
import type { ReplayEvent } from "./types";

export type EventTone = "info" | "pit" | "contact" | "penalty" | "out" | "finish";

export interface DescribedEvent {
  key: string;
  t: number;
  tone: EventTone;
  title: string;
  detail: string;
  cars: number[];
}

function name(m: ReplayModel, c: number | null | undefined): string {
  if (c == null) return "?";
  return m.header.cars[c]?.driver ?? `#${c}`;
}

function compoundAt(m: ReplayModel, c: number, t: number): string | null {
  const k = m.indexAt(t - 0.25);
  if (k < 0) return null;
  const idx = m.field(c, k, "compound");
  return Number.isFinite(idx) ? m.header.compounds[idx] ?? null : null;
}

/** Les pénalités consécutives à un contact sont fusionnées avec celui-ci. */
function mergedPenalty(events: ReplayEvent[], i: number): ReplayEvent | null {
  const e = events[i];
  if (e.type !== "contact" || e.fault == null) return null;
  for (let j = i + 1; j < events.length && events[j].t - e.t < 0.05; j++) {
    const p = events[j];
    if (p.type === "penalty" && p.car === e.fault) return p;
  }
  return null;
}

function isMergedPenalty(events: ReplayEvent[], i: number): boolean {
  const p = events[i];
  if (p.type !== "penalty") return false;
  for (let j = i - 1; j >= 0 && p.t - events[j].t < 0.05; j--) {
    const e = events[j];
    if (e.type === "contact" && e.fault === p.car) return true;
  }
  return false;
}

export function describeEvent(m: ReplayModel, events: ReplayEvent[], i: number, winnerCar: number | null): DescribedEvent | null {
  const e = events[i];
  const key = `${e.t}-${e.type}-${e.car ?? (e.cars ?? []).join("_")}-${i}`;
  const base = { key, t: e.t };
  switch (e.type) {
    case "pit_in": {
      const c = e.car!;
      const from = compoundAt(m, c, e.t);
      const to = e.compound ?? null;
      const tyres = from && to ? ` (${COMPOUND_PLURAL[from] ?? from} → ${COMPOUND_PLURAL[to] ?? to})` : "";
      return {
        ...base,
        tone: "pit",
        title: "Arrêt aux stands",
        detail: `${name(m, c)}${tyres}${e.repair ? ", avec réparation" : ""}`,
        cars: [c],
      };
    }
    case "pit_out":
      return { ...base, tone: "pit", title: "Sortie des stands", detail: name(m, e.car), cars: [e.car!] };
    case "contact": {
      const [a, b] = e.cars ?? [];
      const pen = mergedPenalty(events, i);
      const kind = e.kind && e.kind !== "rear" && e.kind !== "side" ? ` (${CONTACT_KIND[e.kind] ?? e.kind})` : "";
      let detail = `${name(m, a)} / ${name(m, b)}`;
      if (e.fault == null) detail += ", incident de course";
      else detail += `, fautif : ${name(m, e.fault)}${pen ? ` (+${pen.seconds ?? 5} s)` : ""}`;
      return { ...base, tone: "contact", title: `Contact${kind}`, detail, cars: [a, b].filter((x) => x != null) };
    }
    case "penalty":
      if (isMergedPenalty(events, i)) return null;
      return { ...base, tone: "penalty", title: "Pénalité", detail: `${name(m, e.car)} (+${e.seconds ?? 5} s)`, cars: [e.car!] };
    case "out":
      return {
        ...base,
        tone: "out",
        title: "Abandon",
        detail: `${name(m, e.car)} (${ELIM_REASON_SHORT[e.reason ?? ""] ?? e.reason ?? "raison inconnue"})`,
        cars: [e.car!],
      };
    case "dsq":
      return {
        ...base,
        tone: "out",
        title: "Disqualification",
        detail: `${name(m, e.car)} (${ELIM_REASON_SHORT[e.reason ?? ""] ?? e.reason ?? "décision des commissaires"})`,
        cars: [e.car!],
      };
    case "team_penalty": {
      const car = e.car != null ? m.header.cars[e.car] : null;
      const team = car?.team ?? (typeof e.team === "string" ? e.team : "Écurie");
      return {
        ...base,
        tone: "penalty",
        title: "Pénalité d'écurie",
        detail: `${team} : −${e.points ?? 15} points au championnat`,
        cars: e.car != null ? [e.car] : [],
      };
    }
    case "finish":
      if (e.car === winnerCar) {
        return { ...base, tone: "finish", title: "Drapeau à damier", detail: `${name(m, e.car)} remporte la course`, cars: [e.car!] };
      }
      return { ...base, tone: "finish", title: "Arrivée", detail: name(m, e.car), cars: [e.car!] };
    default:
      return { ...base, tone: "info", title: e.type, detail: e.car != null ? name(m, e.car) : "", cars: e.car != null ? [e.car] : [] };
  }
}

export function winnerOf(m: ReplayModel): number | null {
  let w: number | null = null;
  let wt = Infinity;
  m.finishT.forEach((ft, c) => {
    if (ft != null && ft < wt) {
      wt = ft;
      w = c;
    }
  });
  return w;
}

/** Les n derniers événements jusqu'à t (le plus récent en premier). */
export function latestEvents(m: ReplayModel, t: number, n = 6): DescribedEvent[] {
  const ev = m.events;
  const out: DescribedEvent[] = [];
  const w = winnerOf(m);
  let i = ev.length - 1;
  while (i >= 0 && ev[i].t > t) i--;
  for (; i >= 0 && out.length < n; i--) {
    const d = describeEvent(m, ev, i, w);
    if (d) out.push(d);
  }
  return out;
}

/** Marqueurs de la frise : contacts, arrêts, abandons/disqualifications. */
export function timelineMarkers(m: ReplayModel): { t: number; tone: EventTone; label: string }[] {
  const out: { t: number; tone: EventTone; label: string }[] = [];
  const w = winnerOf(m);
  m.events.forEach((e, i) => {
    if (e.type !== "contact" && e.type !== "pit_in" && e.type !== "out" && e.type !== "dsq") return;
    const d = describeEvent(m, m.events, i, w);
    if (d) out.push({ t: e.t, tone: d.tone, label: `${d.title} — ${d.detail}` });
  });
  return out;
}
