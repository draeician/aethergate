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
const SIGNED_DECIMAL_RE = /^-?[0-9]+(?:\.[0-9]+)?$/;
const ZERO_RE = /^0+(\.0+)?$/;

/** True when `value` is a syntactically valid non-negative Decimal string. */
export function isValidDecimalString(value: string): boolean {
  return DECIMAL_RE.test(value.trim());
}

/** True when `value` is a syntactically valid positive (non-zero) Decimal string. */
export function isPositiveDecimalString(value: string): boolean {
  if (!isValidDecimalString(value)) return false;
  return DECIMAL_RE.test(value.trim()) && !ZERO_RE.test(value.trim());
}

/**
 * Classify a signed fixed-point Decimal string relative to zero without any
 * IEEE-754 numeric coercion.
 *
 * Returns:
 * - `-1` for an exact negative value (e.g. `"-0.000000000001"`);
 * - `0` for an exact zero value, including negative zero forms (`"-0"`,
 *   `"-0.000000000000"`);
 * - `1` for an exact positive value (e.g. `"0.000000000001"`).
 *
 * Malformed input (anything that is not a canonical signed Decimal string,
 * such as `""`, `"1e-3"`, `"NaN"`, or `"1.2.3"`) is classified as `0`
 * ("not positive") so callers fail closed rather than treating garbage as an
 * available balance.
 */
export function compareDecimalToZero(value: string): -1 | 0 | 1 {
  const trimmed = value.trim();
  if (!SIGNED_DECIMAL_RE.test(trimmed)) return 0;
  const negative = trimmed[0] === "-";
  const unsigned = negative ? trimmed.slice(1) : trimmed;
  if (ZERO_RE.test(unsigned)) return 0;
  return negative ? -1 : 1;
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
