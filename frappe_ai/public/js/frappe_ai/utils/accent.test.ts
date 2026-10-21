import { describe, expect, it } from "vitest";
import { accentForeground, isAccent } from "./accent";

describe("accentForeground", () => {
  it("puts dark ink on a light accent and white on a deep one", () => {
    expect(accentForeground("#14b8a6")).toBe("#0f172a"); // teal: never white text
    expect(accentForeground("#fff")).toBe("#0f172a");
    expect(accentForeground("#1e3a8a")).toBe("#ffffff");
    expect(accentForeground("#000000")).toBe("#ffffff");
  });

  it("ignores anything that is not a hex colour", () => {
    expect(accentForeground("teal")).toBeNull();
    expect(accentForeground("red; background:url(x)")).toBeNull();
    expect(isAccent("#14b8a6")).toBe(true);
    expect(isAccent("")).toBe(false);
    expect(isAccent(undefined)).toBe(false);
  });
});
