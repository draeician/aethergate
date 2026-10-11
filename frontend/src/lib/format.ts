/** Presentation-only formatting helpers (no secrets, no content). */

export function formatDuration(totalSeconds: number | null | undefined): string {
  if (totalSeconds === null || totalSeconds === undefined || totalSeconds < 0) return "—";
  if (totalSeconds < 1) return "<1s";
  if (totalSeconds < 60) return `${Math.round(totalSeconds)}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  if (minutes < 60) return `${minutes}m ${seconds}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

/** Human label for a scheduler request state. */
export function stateLabel(state: string): string {
  return state.replaceAll("_", " ");
}

/** Human label for an effective wait reason (never raw content). */
export function waitReasonLabel(reason: string | null | undefined): string {
  switch (reason) {
    case "endpoint_paused":
      return "Endpoint paused";
    case "endpoint_draining":
      return "Endpoint draining";
    case null:
    case undefined:
      return "—";
    default:
      return reason.replaceAll("_", " ");
  }
}

export function operationalStateLabel(state: string): string {
  return state;
}

/** Format a millisecond percentile; null means no samples (never a fake 0ms). */
export function formatMillis(value: number | null | undefined): string {
  if (value === null || value === undefined) return "No samples";
  if (value < 1) return "<1ms";
  if (value < 1000) return `${Math.round(value)}ms`;
  return `${(value / 1000).toFixed(2)}s`;
}

/** Format a 0..1 rate as a percentage; null means no samples (never fake 100%). */
export function formatRate(value: number | null | undefined): string {
  if (value === null || value === undefined) return "No samples";
  return `${(value * 100).toFixed(1)}%`;
}
