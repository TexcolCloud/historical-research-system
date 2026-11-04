// @vitest-environment happy-dom
import { test, expect } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { RenderedMarkdown } from "./RenderedMarkdown.tsx";
import { resolveLocation } from "./review-highlights.ts";

test("highlights the contextual occurrence across formatted text without changing Markdown", () => {
  const markdown = "😀甲石**待辨**渡口。乙石**待辨**渡口。";
  const { container } = render(
    <RenderedMarkdown
      markdown={markdown}
      highlights={[
        {
          id: "one",
          page: 1,
          anchor: { exact: "石**待辨**渡口", prefix: "乙", suffix: "" },
          focus: "待辨",
          boxes: [],
          image_precision: "page",
          message: "待核",
          source_reading: "",
          candidate: "",
          suggestion: "",
        },
      ]}
      activeHighlight="one"
    />,
  );
  expect(container.querySelectorAll("mark")).toHaveLength(1);
  expect(container.querySelector("mark")?.textContent).toBe("待辨");
  expect(container.querySelector("mark")?.closest("strong")).not.toBeNull();
  expect(container.textContent).toBe("😀甲石待辨渡口。乙石待辨渡口。\n");
  cleanup();
});

test("missing character uses a caret with Unicode-safe editing position", () => {
  const issue = {
    id: "insert",
    page: 1,
    anchor: { exact: "石渡口", prefix: "", suffix: "" },
    focus: "",
    insertion_offset: 1,
    boxes: [],
    image_precision: "page",
    message: "缺字",
    source_reading: "石岔渡口",
    candidate: "石渡口",
    suggestion: "岔",
  };
  expect(resolveLocation("😀石渡口", issue)).toEqual({ start: 3, end: 3 });
  const { container } = render(
    <RenderedMarkdown
      markdown="😀石渡口"
      highlights={[issue]}
      activeHighlight="insert"
    />,
  );
  expect(
    container.querySelector("mark[data-review-insertion]")?.textContent,
  ).toBe("▏");
  cleanup();
});

test("footnote definition highlight keeps its source anchor and backlink", () => {
  const markdown = "甲[^1]。\n\n[^1]: 原注待辨。";
  const at = markdown.indexOf("[^1]:");
  const issue = {
    id: "note",
    page: 1,
    anchor: { exact: "[^1]: 原注待辨。", prefix: "", suffix: "" },
    focus: "待辨",
    boxes: [],
    image_precision: "page",
    message: "原注疑字",
    source_reading: "",
    candidate: "",
    suggestion: "",
  };
  const { container } = render(
    <RenderedMarkdown
      markdown={markdown}
      footnotes={[
        {
          id: "1",
          label: "1",
          note: { start: at, end: markdown.length, marker_end: at + 6 },
          references: [{ start: 1, end: 5 }],
        },
      ]}
      highlights={[issue]}
    />,
  );
  expect(container.querySelector("mark")?.textContent).toBe("待辨");
  expect(container.querySelectorAll("a[data-note-jump]")).toHaveLength(2);
  cleanup();
});
