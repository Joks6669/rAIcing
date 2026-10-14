// Petits composants de présentation, utilisables côté serveur et côté client.
import type { CSSProperties, ReactNode } from "react";
import { COMPOUND_LETTER, COMPOUND_NAME, ROUND_STATUS } from "@/lib/labels";
import type { RoundStatus } from "@/lib/types";

export function CompoundBadge({ compound, size = "md" }: { compound: string | null | undefined; size?: "sm" | "md" }) {
  if (!compound) return <span className="muted">—</span>;
  return (
    <span className={`tyre tyre-${compound} tyre-${size}`} title={COMPOUND_NAME[compound] ?? compound} aria-label={`Pneus ${COMPOUND_NAME[compound] ?? compound}`}>
      {COMPOUND_LETTER[compound] ?? "?"}
    </span>
  );
}

export function TeamSwatch({ color, tall = false }: { color: string; tall?: boolean }) {
  return <span className={tall ? "swatch swatch-tall" : "swatch"} style={{ background: color }} aria-hidden />;
}

export function RoundStatusPill({ status }: { status: RoundStatus }) {
  return <span className={`pill pill-${status}`}>{ROUND_STATUS[status] ?? status}</span>;
}

export function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "accent" | "warn" | "danger" | "live" }) {
  return <span className={`pill pill-tone-${tone}`}>{children}</span>;
}

/** Barre de niveau 0..max découpée en segments. */
export function LevelBar({ value, max = 10, color, label }: { value: number; max?: number; color?: string; label?: string }) {
  return (
    <span className="levelbar" role="img" aria-label={label ?? `${value} sur ${max}`}>
      {Array.from({ length: max }, (_, i) => (
        <span key={i} className={i < value ? "on" : ""} style={i < value && color ? ({ background: color } as CSSProperties) : undefined} />
      ))}
    </span>
  );
}

/** Jauge horizontale continue (valeur / capacité). */
export function Gauge({ value, max, tone = "accent", label }: { value: number; max: number; tone?: "accent" | "warn" | "danger" | "team"; label?: string; color?: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  const t = tone === "accent" && pct > 0.9 ? "warn" : tone;
  return (
    <span className={`gauge gauge-${t}`} role="meter" aria-valuemin={0} aria-valuemax={max} aria-valuenow={value} aria-label={label}>
      <span style={{ width: `${pct * 100}%` }} />
    </span>
  );
}

export function SectionTitle({ children, aside, id }: { children: ReactNode; aside?: ReactNode; id?: string }) {
  return (
    <div className="section-title" id={id}>
      <h2>{children}</h2>
      {aside ? <div className="section-aside">{aside}</div> : null}
    </div>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub ? <div className="stat-sub">{sub}</div> : null}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children ? <div className="empty-body">{children}</div> : null}
    </div>
  );
}
