import { describe, expect, it } from "vitest";
import {
  DASH,
  groupIN,
  inr2,
  inrAbbr,
  inrCompact,
  inrExact,
  inrLakh,
  inrSigned,
  inrUnit,
  inrWhole,
} from "@/shared/format/money";

describe("inrWhole", () => {
  it("rounds to whole rupees with lakh/crore grouping", () => {
    expect(inrWhole(0)).toBe("₹0");
    expect(inrWhole(16243.422)).toBe("₹16,243");
    expect(inrWhole(123456.789)).toBe("₹1,23,457");
    expect(inrWhole(12345678.9)).toBe("₹1,23,45,679");
    expect(inrWhole("100000")).toBe("₹1,00,000");
    expect(inrWhole("50000.75")).toBe("₹50,001");
  });
  it("puts the sign before the symbol", () => {
    expect(inrWhole(-0.6415)).toBe("-₹1");
    expect(inrWhole(-1234.5)).toBe("-₹1,234"); // Math.round(-1234.5) === -1234
    expect(inrWhole(-0.4)).toBe("₹0"); // never "-₹0"
  });
  it("renders null, undefined and non-finite as a dash", () => {
    expect(inrWhole(null)).toBe(DASH);
    expect(inrWhole(undefined)).toBe(DASH);
    expect(inrWhole("abc")).toBe(DASH);
    expect(inrWhole(NaN)).toBe(DASH);
  });
});

describe("inr2", () => {
  it("always shows two decimals", () => {
    expect(inr2(0)).toBe("₹0.00");
    expect(inr2("1234.5")).toBe("₹1,234.50");
    expect(inr2(3162234.181)).toBe("₹31,62,234.18");
  });
  it("signs before the symbol and dashes null", () => {
    expect(inr2(-1234.5678)).toBe("-₹1,234.57");
    expect(inr2(null)).toBe(DASH);
  });
});

describe("inrExact", () => {
  it("keeps the backend's fractional digits verbatim up to 2dp", () => {
    expect(inrExact("13247.63")).toBe("₹13,247.63");
    expect(inrExact("1234567.8")).toBe("₹12,34,567.8");
    expect(inrExact("5000")).toBe("₹5,000");
    expect(inrExact("0")).toBe("₹0");
    expect(inrExact("-0.50")).toBe("-₹0.50");
  });
  it("rounds a long main-ledger amount half-up to paise, from the string", () => {
    expect(inrExact("4999.999999603336")).toBe("₹5,000.00");
    expect(inrExact("-4999.999999603336")).toBe("-₹5,000.00");
    expect(inrExact("1234567.894")).toBe("₹12,34,567.89");
    expect(inrExact("1234567.895")).toBe("₹12,34,567.90");
    expect(inrExact("98.123456")).toBe("₹98.12");
    expect(inrExact("0.004")).toBe("₹0.00");
    expect(inrExact("-0.004")).toBe("₹0.00");
  });
  it("handles negatives and null", () => {
    expect(inrExact("-3000.00")).toBe("-₹3,000.00");
    expect(inrExact(null)).toBe(DASH);
  });
});

describe("inrUnit", () => {
  it("keeps the full precision of a per-unit price", () => {
    expect(inrUnit("123.4567")).toBe("₹123.4567");
    expect(inrUnit("98.123456")).toBe("₹98.123456");
    expect(inrUnit("-1234567.891")).toBe("-₹12,34,567.891");
    expect(inrUnit(null)).toBe(DASH);
  });
});

describe("inrCompact", () => {
  it("uses Cr and L at two decimals, whole rupees below a lakh", () => {
    expect(inrCompact(12345678.9)).toBe("₹1.23Cr");
    expect(inrCompact(123456.789)).toBe("₹1.23L");
    expect(inrCompact(99999)).toBe("₹99,999");
    expect(inrCompact(7890)).toBe("₹7,890");
    expect(inrCompact(0)).toBe("₹0");
  });
  it("signs before the symbol and dashes null", () => {
    expect(inrCompact(-12345678.9)).toBe("-₹1.23Cr");
    expect(inrCompact(-123456.789)).toBe("-₹1.23L");
    expect(inrCompact(-1234.5)).toBe("-₹1,234");
    expect(inrCompact(null)).toBe(DASH);
  });
});

describe("inrLakh", () => {
  it("is always lakhs at two decimals", () => {
    expect(inrLakh(1250000)).toBe("₹12.50L");
    expect(inrLakh(50000)).toBe("₹0.50L");
    expect(inrLakh(-250000)).toBe("-₹2.50L");
    expect(inrLakh(undefined)).toBe(DASH);
  });
});

describe("inrSigned", () => {
  it("carries direction with a true minus sign", () => {
    expect(inrSigned(1234567)).toBe("+₹12,34,567");
    expect(inrSigned(-500)).toBe("−₹500");
  });
  it("shows zero without a sign", () => {
    expect(inrSigned(0)).toBe("₹0");
    expect(inrSigned(null)).toBe(DASH);
  });
});

describe("groupIN", () => {
  it("groups digits without a symbol", () => {
    expect(groupIN(1234567)).toBe("12,34,567");
    expect(groupIN("1234567.891")).toBe("12,34,567.891");
    expect(groupIN(1234567.6, { round: true })).toBe("12,34,568");
    expect(groupIN(null)).toBe(DASH);
  });
});

describe("inrAbbr", () => {
  it("abbreviates by tier with one trimmed decimal", () => {
    expect(inrAbbr(230000)).toBe("₹2.3L");
    expect(inrAbbr(24500)).toBe("₹24.5k");
    expect(inrAbbr(900)).toBe("₹900");
    expect(inrAbbr(-12300000)).toBe("-₹1.2Cr");
  });
});
