import { describe, expect, it } from "vitest";
import {
  isValidDecimalString,
  isPositiveDecimalString,
  formatMoney,
  normalizeDecimalInput,
} from "./decimal";

describe("decimal", () => {
  it("accepts awkward high-precision Decimal strings", () => {
    expect(isValidDecimalString("0.000000000123")).toBe(true);
    expect(isValidDecimalString("123456789.123456789012")).toBe(true);
    expect(isValidDecimalString("0")).toBe(true);
    expect(isValidDecimalString("42.5")).toBe(true);
  });

  it("rejects non-decimal and binary-float-incompatible input", () => {
    expect(isValidDecimalString("")).toBe(false);
    expect(isValidDecimalString("1e-3")).toBe(false);
    expect(isValidDecimalString("0x10")).toBe(false);
    expect(isValidDecimalString("12,34")).toBe(false);
    expect(isValidDecimalString("1.2.3")).toBe(false);
    expect(isValidDecimalString("-1")).toBe(false);
    expect(isValidDecimalString("NaN")).toBe(false);
  });

  it("distinguishes positive from zero", () => {
    expect(isPositiveDecimalString("0.000000000123")).toBe(true);
    expect(isPositiveDecimalString("0")).toBe(false);
    expect(isPositiveDecimalString("0.0")).toBe(false);
    expect(isPositiveDecimalString("")).toBe(false);
  });

  it("preserves exact text without Number coercion (canary)", () => {
    // A value whose exact text binary float would corrupt.
    const value = "123456789.123456789012";
    expect(formatMoney(value, "USD")).toBe("123456789.123456789012 USD");
    expect(normalizeDecimalInput(`  ${value}  `)).toBe(value);
  });

  it("renders a missing amount as an em dash", () => {
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney(undefined, "USD")).toBe("—");
    expect(formatMoney("")).toBe("—");
  });
});
