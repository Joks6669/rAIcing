// Libellés français des valeurs du contrat d'API.
import type { Category, PartKey, RoundStatus } from "./types";

export const ROUND_STATUS: Record<RoundStatus, string> = {
  scheduled: "Programmée",
  entries_open: "Inscriptions ouvertes",
  running: "En cours",
  done: "Courue",
};

export const SEASON_STATUS: Record<string, string> = {
  planned: "En préparation",
  active: "En cours",
  finished: "Terminée",
};

export const CATEGORY: Record<Category, string> = {
  rapide: "Rapide",
  mixte: "Mixte",
  technique: "Technique",
};

export const CATEGORY_HINT: Record<Category, string> = {
  rapide: "longues lignes droites, vitesse moyenne élevée",
  mixte: "équilibre entre vitesse et virages",
  technique: "virages serrés, vitesse moyenne faible",
};

export const COMPOUND_NAME: Record<string, string> = {
  soft: "Tendre",
  medium: "Medium",
  hard: "Dur",
  wet: "Pluie",
};

/** Formes plurielles pour les phrases (« tendres → durs »). */
export const COMPOUND_PLURAL: Record<string, string> = {
  soft: "tendres",
  medium: "medium",
  hard: "durs",
  wet: "pluie",
};

export const COMPOUND_LETTER: Record<string, string> = {
  soft: "S",
  medium: "M",
  hard: "H",
  wet: "W",
};

export const ELIM_REASON_SHORT: Record<string, string> = {
  wall: "rebord",
  tyre: "pneu détruit",
  broken: "voiture cassée",
  stalled: "arrêt prolongé",
  wrong_way: "demi-tour",
  caused_elimination: "élimination provoquée",
};

const DNF_LONG: Record<string, string> = {
  wall: "Abandon — contact avec un rebord",
  tyre: "Pneu détruit",
  broken: "Voiture cassée",
  stalled: "Abandon — arrêt prolongé",
  wrong_way: "Abandon — demi-tour",
};

/** Statut + raison d'une ligne de classement de course, en français. */
export function raceStatusLabel(status: string, reason: string): string {
  if (status === "finished") return "Arrivé";
  if (status === "dsq") {
    if (reason === "caused_elimination") return "Disqualifié — élimination provoquée";
    return reason ? `Disqualifié — ${ELIM_REASON_SHORT[reason] ?? reason}` : "Disqualifié";
  }
  if (status === "dnf") return DNF_LONG[reason] ?? (reason ? `Abandon — ${reason}` : "Abandon");
  if (status === "racing") return "En piste";
  if (status === "pit") return "Aux stands";
  return status;
}

export function qualiStatusLabel(status: string, reason: string): string {
  if (status === "timed") return "Chronométré";
  if (reason) return `Sans temps — ${ELIM_REASON_SHORT[reason] ?? reason}`;
  return "Sans temps";
}

export const CONTACT_KIND: Record<string, string> = {
  rear: "par l'arrière",
  side: "côte à côte",
  brake_check: "freinage injustifié",
};

export const PARTS: { key: PartKey; label: string; effect: string }[] = [
  { key: "engine", label: "Moteur", effect: "Puissance" },
  { key: "aero", label: "Appui aérodynamique", effect: "Appui" },
  { key: "bodywork", label: "Carrosserie basse traînée", effect: "Traînée" },
  { key: "chassis", label: "Châssis", effect: "Masse et capacité d'intégration" },
  { key: "brakes", label: "Freins", effect: "Force de freinage" },
  { key: "cooling", label: "Refroidissement", effect: "Capacité thermique, pneus" },
  { key: "suspension", label: "Suspension", effect: "Adhérence mécanique, usure" },
];

export const TECHNOLOGIES: Record<string, string> = {
  composites: "Matériaux composites",
  active_cooling: "Refroidissement actif",
  low_drag: "Aileron basse traînée",
  carbon_brakes: "Freins carbone",
  tyre_telemetry: "Télémétrie pneumatique",
  energy_recovery: "Récupération d'énergie",
};

export const METHODS: Record<string, string> = {
  ppo: "PPO",
  sac: "SAC",
  td3: "TD3",
  es: "Stratégies d'évolution",
  bc: "Imitation",
  reference: "Pilote de référence",
};

export const RATING_COMPONENTS: { key: "speed" | "consistency" | "survival" | "rain" | "tyres" | "traffic"; label: string; hint: string }[] = [
  { key: "speed", label: "Vitesse pure", hint: "Temps au tour, seul en piste" },
  { key: "consistency", label: "Régularité", hint: "Dispersion des temps au tour" },
  { key: "survival", label: "Survie", hint: "Capacité à finir sans abandon" },
  { key: "rain", label: "Pilotage sous la pluie", hint: "Rythme sur piste mouillée" },
  { key: "tyres", label: "Gestion des pneus", hint: "Dégradation sur un relais long" },
  { key: "traffic", label: "Compétition et trafic", hint: "Course au contact d'adversaires" },
];
