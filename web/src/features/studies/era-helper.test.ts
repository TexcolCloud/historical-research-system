import { expect, test } from "vitest";
import { yearReference } from "./era-helper";

test("a Gregorian year keeps cross-era candidates and never pretends to convert a missing day", () => {
  const value = yearReference(1875);
  expect(value.ganzhi).toBe("乙亥");
  expect(value.eras).toEqual(["同治十三年（年初候选）", "光绪元年"]);
  expect(value.caution).toContain("月日");
  expect(() => yearReference(1875.5)).toThrow();
});
