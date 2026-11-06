// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { RenderedMarkdown } from "./RenderedMarkdown.tsx";
import {
  readLocations,
  moveReviewLocations,
  recoverReviewLocations,
  renderedRanges,
  sourceRanges,
  type ReviewLocation,
} from "./review-highlights.ts";

afterEach(cleanup);

function issue(
  exact: string,
  focus = exact,
  prefix = "",
  suffix = "",
): ReviewLocation {
  return {
    id: "observed",
    page: 1,
    anchor: { exact, prefix, suffix },
    focus,
    boxes: [],
    image_precision: "page",
    message: "核对这处原文",
    source_reading: "",
    candidate: exact,
    suggestion: "",
  };
}

test("a descriptive focus falls back to the unique source phrase and malformed boxes retain the doubt", () => {
  const rows = readLocations([
    { ...issue("某位人名", "Personal name"), boxes: [[0.1, 0.2, 1.2, 0.4]] },
  ]);
  expect(rows).toHaveLength(1);
  expect(rows[0].boxes).toEqual([]);
  expect(rows[0].location_diagnostics).toContain("invalid_image_box");
  expect(sourceRanges("记载某位人名。", rows[0])[0]).toEqual({
    start: 2,
    end: 6,
  });
});

test("canonical codepoint ranges convert to UTF16 and a changed source reanchors rather than trusting stale offsets", () => {
  const [location] = readLocations(
    [
      {
        ...issue("甲错乙", "错"),
        start: 2,
        end: 3,
        offset_unit: "unicode_codepoint",
        text_sha256: "source-hash",
      },
    ],
    "😀甲错乙。",
    "source-hash",
  );
  expect(sourceRanges("😀甲错乙。", location)[0]).toEqual({ start: 3, end: 4 });
  expect(sourceRanges("新前言😀甲错乙。", location)[0]).toEqual({
    start: 6,
    end: 7,
  });
});

test.each([
  ["前段。\n\n错字。", issue("错字", "错", "前段。\n\n"), "错"],
  ["# 同名\n\n正文同名。", issue("同名", "同名", "# "), "同名"],
])(
  "full source mapping survives block syntax and contextual duplicate text: %s",
  (markdown, location, expected) => {
    const { container } = render(
      <RenderedMarkdown markdown={markdown} highlights={[location]} />,
    );
    expect(container.querySelector("mark[data-review-id]")?.textContent).toBe(
      expected,
    );
  },
);

test("footnote replacement does not erase a contextual note-body source location", () => {
  const markdown = "正文[^1]后错字。\n\n[^1]: 原注。";
  const at = markdown.indexOf("[^1]:");
  const { container } = render(
    <RenderedMarkdown
      markdown={markdown}
      highlights={[issue("原注", "原", "[^1]: ")]}
      footnotes={[
        {
          id: "note",
          label: "1",
          note: { start: at, end: markdown.length, marker_end: at + 6 },
          references: [{ start: 2, end: 6 }],
        },
      ]}
    />,
  );
  expect(container.querySelector("mark[data-review-id]")?.textContent).toBe(
    "原",
  );
  expect(container.querySelectorAll("a[data-note-jump]")).toHaveLength(2);
});

test("mapping failure is reported instead of silently hiding a source-only attribute doubt", () => {
  const failed = vi.fn();
  const { container } = render(
    <RenderedMarkdown
      markdown={"[链接](https://example.test/错字)"}
      highlights={[issue("错字", "错")]}
      onLocationFailure={failed}
    />,
  );
  expect(container.querySelector("mark[data-review-id]")).toBeNull();
  expect(failed).toHaveBeenLastCalledWith(["observed"]);
});

test("one doubt can decorate multiple explicitly supplied source spans", () => {
  const location = {
    ...issue("甲错乙", "错"),
    text_spans: [
      { anchor: issue("甲错乙", "错").anchor, focus: "错" },
      { anchor: issue("丙错丁", "错").anchor, focus: "错" },
    ],
  };
  const { container } = render(
    <RenderedMarkdown markdown="甲错乙，丙错丁。" highlights={[location]} />,
  );
  expect(
    [...container.querySelectorAll("mark[data-review-id]")].map(
      (x) => x.textContent,
    ),
  ).toEqual(["错", "错"]);
});

test("legacy multi-edit drafts recover each unique unchanged context independently", () => {
  const original = "😀甲错乙，丙错丁。";
  const final = "😀甲修正乙，丙订正丁。";
  const recovered = recoverReviewLocations(original, final, [
    { ...issue("甲错乙", "错"), id: "first" },
    { ...issue("丙错丁", "错"), id: "second" },
  ]);
  expect(
    recovered.map((x) => {
      const range = sourceRanges(final, x)[0];
      return range && final.slice(range.start, range.end);
    }),
  ).toEqual(["修正", "订正"]);
  expect(
    moveReviewLocations(original, final, [issue("甲错乙", "错")])[0].focus,
  ).toBe("修正");
});

test("a bound canonical range disambiguates repeated source text, while a wrong hash cannot", () => {
  const raw = {
    ...issue("错"),
    start: 3,
    end: 4,
    offset_unit: "unicode_codepoint",
    text_sha256: "current",
  };
  const text = "😀错甲错。";
  const [current] = readLocations([raw], text, "current");
  expect(sourceRanges(text, current)[0]).toEqual({ start: 4, end: 5 });
  const [old] = readLocations([raw], text, "changed");
  expect(sourceRanges(text, old)[0]).toBeNull();
  expect(old.location_diagnostics).toContain("source_hash_mismatch");
});

test("corrupt optional text metadata downgrades without dropping an otherwise usable doubt", () => {
  const [location] = readLocations([
    {
      ...issue("错字", "Label"),
      start: "broken",
      offset_unit: "utf16",
      text_spans: "broken",
    },
  ]);
  expect(location.location_diagnostics).toContain("invalid_text_metadata");
  expect(sourceRanges("甲错字乙", location)[0]).toEqual({ start: 1, end: 3 });
});

test("visual editor projection handles its omitted block serializer whitespace", () => {
  expect(
    renderedRanges(
      "同名正文同名。",
      "# 同名\n\n正文同名。",
      issue("同名", "同名", "# "),
      undefined,
      true,
    ),
  ).toEqual([{ start: 0, end: 2 }]);
});

test("unrecoverable old drafts keep the doubt with an explicit missing diagnostic", () => {
  const [location] = recoverReviewLocations("甲错乙", "完全重写", [
    issue("甲错乙", "错"),
  ]);
  expect(location.text_status).toBe("missing");
  expect(location.location_diagnostics).toContain("draft_location_unresolved");
  expect(sourceRanges("完全重写", location)[0]).toBeNull();
});

test("Unicode codepoint insertion survives footnote replacement after an emoji", () => {
  const markdown = "😀正文[^1]。\n\n[^1]: 原注待辨。";
  const note = Array.from(markdown.slice(0, markdown.indexOf("[^1]:"))).length;
  const [location] = readLocations(
    [
      {
        ...issue("原注待辨", "", "[^1]: ", "。"),
        insertion_offset: 2,
        start: note + 8,
        end: note + 8,
        offset_unit: "unicode_codepoint",
        text_sha256: "bound",
      },
    ],
    markdown,
    "bound",
  );
  const failed = vi.fn();
  const { container } = render(
    <RenderedMarkdown
      markdown={markdown}
      highlights={[location]}
      onLocationFailure={failed}
      footnotes={[
        {
          id: "1",
          label: "1",
          note: {
            start: note,
            end: Array.from(markdown).length,
            marker_end: note + 6,
          },
          references: [{ start: 3, end: 7 }],
        },
      ]}
    />,
  );
  expect(
    container.querySelector("mark[data-review-insertion]")?.textContent,
  ).toBe("▏");
  expect(container.textContent).toContain("原注▏待辨");
  expect(failed).toHaveBeenLastCalledWith([]);
});

test("a dataclass null insertion offset stays a text highlight", () => {
  const [location] = readLocations([
    { ...issue("错字", "错"), insertion_offset: null },
  ]);
  const { container } = render(
    <RenderedMarkdown markdown="甲错字乙" highlights={[location]} />,
  );
  expect(container.querySelector("mark")?.textContent).toBe("错");
  expect(container.querySelector("mark[data-review-insertion]")).toBeNull();
});
