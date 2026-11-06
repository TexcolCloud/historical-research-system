import { expect, test } from "vitest";
import {
  moveReviewLocations,
  recoverReviewLocations,
  sourceRanges,
  type ReviewLocation,
} from "./review-highlights.ts";

function partialLocation(): ReviewLocation {
  const missing = {
    anchor: { exact: "", prefix: "", suffix: "" },
    focus: "",
    text_status: "missing" as const,
    unlocated_reason: "原件缺损，第一处无法对应候选。",
  };
  return {
    id: "one-stable-doubt",
    page: 1,
    ...missing,
    boxes: [],
    image_precision: "page",
    message: "两处机构名待核。",
    source_reading: "",
    candidate: "",
    suggestion: "",
    text_spans: [
      missing,
      {
        anchor: { exact: "丙错丁", prefix: "", suffix: "" },
        focus: "错",
        text_status: "located",
      },
    ],
  };
}

test.each([
  ["editing", moveReviewLocations],
  ["old draft restore", recoverReviewLocations],
] as const)(
  "%s preserves and moves later explicit spans when the first one is unlocated",
  (_label, map) => {
    const before = "甲错乙，丙错丁。";
    const after = "序甲错乙，丙对丁。";
    const location = partialLocation();
    expect(sourceRanges(before, location)).toEqual([
      null,
      { start: 5, end: 6 },
    ]);
    const [updated] = map(before, after, [location]);
    expect(updated.id).toBe(location.id);
    expect(sourceRanges(after, updated)).toEqual([null, { start: 6, end: 7 }]);
    expect(updated.text_spans?.[0].text_status).toBe("missing");
    expect(updated.text_spans?.[1].focus).toBe("对");
    expect(updated.text_spans?.[1].location_diagnostics).not.toContain(
      "draft_location_unresolved",
    );
  },
);

test("an unresolved individual span remains suppressed without hiding a usable sibling", () => {
  const location = partialLocation();
  location.text_spans![0] = {
    anchor: { exact: "甲错乙", prefix: "", suffix: "" },
    focus: "错",
    location_diagnostics: ["draft_location_unresolved"],
  };
  expect(sourceRanges("甲错乙，丙错丁。", location)).toEqual([
    null,
    { start: 5, end: 6 },
  ]);
});
