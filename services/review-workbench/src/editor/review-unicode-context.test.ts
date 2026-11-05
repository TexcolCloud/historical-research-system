import { expect, it } from "vitest";
import { moveReviewLocations, recoverReviewLocations, type ReviewLocation } from "./review-highlights";

it("keeps generated draft context on Unicode boundaries", () => {
  const before = "😀" + "甲".repeat(31) + "错乙";
  const after = before.replace("错", "对");
  const issue: ReviewLocation = {
    id: "unicode-context", page: 1,
    anchor: { exact: "错", prefix: "甲".repeat(31), suffix: "乙" },
    focus: "错", boxes: [], image_precision: "page",
    message: "待核", source_reading: "对", candidate: "错", suggestion: "对",
  };
  for (const update of [moveReviewLocations, recoverReviewLocations]) {
    const location = update(before, after, [issue])[0];
    for (const text of Object.values(location.anchor)) {
      expect(() => encodeURIComponent(text)).not.toThrow();
    }
    expect(location.focus).toBe("对");
  }
});
