import { describe, expect, it } from "vitest";
import {
  isValidDecimalString,
  isPositiveDecimalString,
  compareDecimalToZero,
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

  it("classifies signed Decimal strings exactly without Number coercion", () => {
    expect(compareDecimalToZero("0")).toBe(0);
    expect(compareDecimalToZero("0.000000000000")).toBe(0);
    expect(compareDecimalToZero("-0")).toBe(0);
    expect(compareDecimalToZero("-0.000000000000")).toBe(0);
    expect(compareDecimalToZero("0.000000000001")).toBe(1);
    expect(compareDecimalToZero("-0.000000000001")).toBe(-1);
    expect(compareDecimalToZero("123456789.123456789012")).toBe(1);
    expect(compareDecimalToZero("-123456789.123456789012")).toBe(-1);
  });

  it("handles malformed signed input safely", () => {
    expect(compareDecimalToZero("")).toBe(0);
    expect(compareDecimalToZero("1e-3")).toBe(0);
    expect(compareDecimalToZero("0x10")).toBe(0);
    expect(compareDecimalToZero("NaN")).toBe(0);
    expect(compareDecimalToZero("Infinity")).toBe(0);
    expect(compareDecimalToZero("12,34")).toBe(0);
    expect(compareDecimalToZero("1.2.3")).toBe(0);
    expect(compareDecimalToZero("-")).toBe(0);
    expect(compareDecimalToZero(".5")).toBe(0);
    expect(compareDecimalToZero("5.")).toBe(0);
  });
});
