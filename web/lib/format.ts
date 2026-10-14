// Formats d'affichage (français). Fonctions pures, utilisables côté serveur comme côté client.

const nf = (min: number, max = min) =>
  new Intl.NumberFormat("fr-FR", { minimumFractionDigits: min, maximumFractionDigits: max });

const NF0 = nf(0);
const NF1 = nf(1);

/** 83.456 -> "1:23.456" ; 3723.4 -> "1:02:03.400". Convention chronométrage (point décimal). */
export function fmtLapTime(seconds: number | null | undefined, digits = 3): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const neg = seconds < 0;
  const scale = 10 ** digits;
  // on arrondit une seule fois, en unités entières, pour éviter « 0:60.000 »
  const units = Math.round(Math.abs(seconds) * scale);
  const totalSec = Math.floor(units / scale);
  const frac = units - totalSec * scale;
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const sInt = totalSec % 60;
  const secPad =
    String(sInt).padStart(2, "0") + (digits > 0 ? "." + String(frac).padStart(digits, "0") : "");
  const core = h > 0 ? `${h}:${String(m).padStart(2, "0")}:${secPad}` : `${m}:${secPad}`;
  return neg ? `-${core}` : core;
}

/** Horloge de course : 132.4 -> "2:12.4" */
export function fmtClock(seconds: number): string {
  return fmtLapTime(Math.max(0, seconds), 1);
}

/** "+3.214 s" */
export function fmtGapSeconds(seconds: number, digits = 3): string {
  return `+${seconds.toFixed(digits)}\u00a0s`;
}

export function fmtLapsDown(n: number): string {
  return `+${n}\u00a0${n > 1 ? "tours" : "tour"}`;
}

export function fmtInt(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return NF0.format(n);
}

export function fmt1(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return NF1.format(n);
}

export function fmtPercent(fraction: number, digits = 0): string {
  return `${nf(digits).format(fraction * 100)}\u202f%`;
}

/** 40000000 -> "40 M" ; 12500000 -> "12,5 M" */
export function fmtMillions(n: number): string {
  return `${nf(0, 1).format(n / 1e6)}\u00a0M`;
}

/** 3261.6 -> "3,26 km" */
export function fmtKm(meters: number): string {
  return `${nf(2).format(meters / 1000)}\u00a0km`;
}

const DATE_LONG = new Intl.DateTimeFormat("fr-FR", {
  weekday: "long",
  day: "numeric",
  month: "long",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Paris",
});
const DATE_SHORT = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", timeZone: "Europe/Paris" });
const DATE_DAY = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "long", year: "numeric", timeZone: "Europe/Paris" });

export function fmtDateLong(iso: string | null | undefined): string {
  if (!iso) return "Date à confirmer";
  const s = DATE_LONG.format(new Date(iso));
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function fmtDateShort(iso: string | null | undefined): string {
  if (!iso) return "—";
  return DATE_SHORT.format(new Date(iso));
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return DATE_DAY.format(new Date(iso));
}

export function ordinal(n: number): string {
  return n === 1 ? "1er" : `${n}e`;
}

/** Points avec signe pour les pénalités : 15 -> "−15" */
export function fmtPenaltyPoints(n: number): string {
  return `−${n}`;
}

/** "1 pt", "25 pts" */
export function fmtPts(n: number): string {
  return `${n}\u00a0${Math.abs(n) > 1 ? "pts" : "pt"}`;
}
