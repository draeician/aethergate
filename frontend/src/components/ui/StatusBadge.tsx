export type BadgeTone = "active" | "inactive" | "warning" | "neutral";

const toneClasses: Record<BadgeTone, string> = {
  active: "bg-[var(--ag-success)]/10 text-[var(--ag-success)]",
  inactive: "bg-[var(--ag-text-muted)]/15 text-[var(--ag-text-muted)]",
  warning: "bg-[var(--ag-warning)]/10 text-[var(--ag-warning)]",
  neutral: "bg-[var(--ag-accent)]/10 text-[var(--ag-accent)]",
};

/** Text-first status badge; color is never the sole indicator (label is visible). */
export default function StatusBadge({ label, tone }: { label: string; tone: BadgeTone }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded-md font-medium ${toneClasses[tone]}`}>{label}</span>
  );
}
