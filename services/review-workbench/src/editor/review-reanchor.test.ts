import { expect, test } from "vitest";
import {
  moveReviewLocations,
  resolveLocation,
  type ReviewLocation,
} from "./review-highlights.ts";
test("editing one doubt keeps the neighboring doubt located even when its original context changes", () => {
  const first: ReviewLocation = {
    id: "first",
    page: 1,
    anchor: { exact: "甲错乙", prefix: "😀", suffix: "，丙错丁" },
    focus: "错",
    boxes: [],
    image_precision: "page",
    message: "",
    source_reading: "",
    candidate: "",
    suggestion: "",
  };
  const second = {
    ...first,
    id: "second",
    anchor: { exact: "丙错丁", prefix: "😀甲错乙，", suffix: "。" },
  };
  const updated = moveReviewLocations(
    "😀甲错乙，丙错丁。",
    "😀甲修订乙，丙错丁。",
    [first, second],
  );
  const a = resolveLocation("😀甲修订乙，丙错丁。", updated[0]);
  const b = resolveLocation("😀甲修订乙，丙错丁。", updated[1]);
  expect(a && "😀甲修订乙，丙错丁。".slice(a.start, a.end)).toBe("修订");
  expect(b && "😀甲修订乙，丙错丁。".slice(b.start, b.end)).toBe("错");
});
