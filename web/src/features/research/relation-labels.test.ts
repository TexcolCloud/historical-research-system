import { expect, test } from "vitest";
import { relationLabel } from "./relation-labels";

test.each([
  ["mentioned_in", "见于材料"],
  ["participant", "参与者"],
  ["reply_to", "回复文书"],
  ["reprint_of", "转载版本"],
  ["same_source", "同源材料"],
  ["support", "支持"],
  ["counter", "反证"],
  ["limit", "限制"],
  ["background", "背景"],
  ["association", "关联"],
  ["same_as", "同一身份"],
  ["possible_same_as", "可能为同一身份"],
])("system relation %s has the user-facing label %s", (value, label) => {
  expect(relationLabel(value)).toBe(label);
  expect(relationLabel(undefined, value)).toBe(label);
});

test("natural-language predicates take precedence over a system kind without substring replacement", () => {
  expect(relationLabel("曾致函请求拨款", "association")).toBe("曾致函请求拨款");
  expect(relationLabel("sent a letter to", "participant")).toBe(
    "sent a letter to",
  );
  expect(relationLabel("participant in the committee", "participant")).toBe(
    "participant in the committee",
  );
  expect(relationLabel("constructor", "association")).toBe("constructor");
});
