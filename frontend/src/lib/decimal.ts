/**
 * Decimal-safe helpers for accounting money.
 *
 * AetherGate money is fixed-point Decimal (Numeric(24,12)). JavaScript numbers
 * are IEEE-754 binary floats and must never stand in for accounting Decimal
 * values: not for editing, not for arithmetic, not for formatting. This module
 * centralizes the rules so no page converts a monetary string through
 * ``Number()``/``parseFloat()``.
 *
 * - Form state for money stays a raw string.
 * - Syntax validation is regex-only (no numeric coercion).
 * - Wire payloads send the exact string (the backend/OpenAPI contract accepts a
 *   string for money fields).
 * - Rendering prints the server-returned string verbatim (no rounding).
 */

const DECIMAL_RE = /^[0-9]+(?:\.[0-9]+)?$/;

/** True when `value` is a syntactically valid non-negative Decimal string. */
export function isValidDecimalString(value: string): boolean {
  return DECIMAL_RE.test(value.trim());
}

/** True when `value` is a syntactically valid positive (non-zero) Decimal string. */
export function isPositiveDecimalString(value: string): boolean {
  if (!isValidDecimalString(value)) return false;
  return DECIMAL_RE.test(value.trim()) && !/^0+(\.0+)?$/.test(value.trim());
}

/**
 * Render a server-returned money string exactly, with its currency when given.
 * Never coerces to a number; a missing value renders as an em dash.
 */
export function formatMoney(
  amount: string | null | undefined,
  currency?: string | null,
): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  return currency ? `${amount} ${currency}` : amount;
}

/** Normalize a user-entered decimal string for a wire payload (trim only). */
export function normalizeDecimalInput(value: string): string {
  return value.trim();
}
