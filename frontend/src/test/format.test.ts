import { describe, expect, it } from "vitest";
import { phone, plural } from "@/lib/format";

describe("phone", () => {
  it("formats the ten digits every source is normalised to", () => {
    expect(phone("7085550124")).toBe("(708) 555-0124");
  });
  it("shows anything else as stored, and says when there is no number", () => {
    expect(phone("555-0124")).toBe("555-0124");
    expect(phone(null)).toBe("No number on file");
  });
});

describe("plural", () => {
  it("uses the singular for one", () => {
    expect(plural(1, "item")).toBe("1 item");
    expect(plural(1200, "item")).toBe("1,200 items");
  });
});
