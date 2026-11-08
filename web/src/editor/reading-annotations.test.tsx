// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RenderedMarkdown } from "./RenderedMarkdown";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { components } from "@/client/schema";
import {
  readingHref,
  readingRange,
  requestedReadingTarget,
  type ReadingTarget,
} from "@/features/library/reader-location";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("a whole footnote definition safely falls back while its visible body maps with unchanged source and note ownership", () => {
  const markdown = "标题[^*]。\n\n[^*]: 星注原文。";
  const start = markdown.indexOf("[^*]:"),
    end = markdown.length;
  const footnotes = [
    {
      id: "star-note",
      label: "*",
      note: { start, end, marker_end: start + 5 },
      references: [
        { start: markdown.indexOf("[^*]"), end: markdown.indexOf("[^*]") + 4 },
      ],
    },
  ];
  const retained = JSON.stringify(footnotes);
  const failed = vi.fn();
  const { container, rerender } = render(
    <RenderedMarkdown
      markdown={markdown}
      footnotes={footnotes}
      readingFocus={{ id: "whole-source-note", start, end }}
      onReadingFailure={failed}
    />,
  );
  const visibleText = container.textContent;
  expect(failed).toHaveBeenLastCalledWith(["whole-source-note"]);
  expect(container.querySelector("mark[data-reading-focus]")).toBeNull();
  rerender(
    <RenderedMarkdown
      markdown={markdown}
      footnotes={footnotes}
      readingFocus={{ id: "visible-note-body", start: start + 6, end }}
      onReadingFailure={failed}
    />,
  );
  expect(failed).toHaveBeenLastCalledWith([]);
  expect(container.querySelector("mark[data-reading-focus]")?.textContent).toBe(
    "星注原文。",
  );
  expect(container.textContent).toBe(visibleText);
  expect(container.querySelector('[id$="star-note"]')).toBeTruthy();
  expect(JSON.stringify(footnotes)).toBe(retained);
});

test("reading links and destination focus preserve Unicode and footnote targets", async () => {
  const markdown = "😀目录条目 …… 12\n\n正文[^1]。\n\n[^1]: 原注。";
  const chars = Array.from(markdown),
    rawNote = chars.join("").indexOf("[^1]:") - 1;
  const note = chars.findIndex(
    (_, i) => chars.slice(i, i + 6).join("") === "[^1]: ",
  );
  const ref = chars.findIndex(
    (_, i) => chars.slice(i, i + 4).join("") === "[^1]",
  );
  const start = chars.findIndex(
    (_, i) => chars.slice(i, i + 2).join("") === "原注",
  );
  expect(note).toBe(rawNote);
  const navigate = vi.fn();
  const { container } = render(
    <RenderedMarkdown
      markdown={markdown}
      footnotes={[
        {
          id: "note",
          label: "1",
          note: { start: note, end: chars.length, marker_end: note + 6 },
          references: [{ start: ref, end: ref + 4 }],
        },
      ]}
      readingLinks={[
        {
          id: "toc",
          start: 1,
          end: 12,
          href: "#/books/book?chapter=body",
          label: "阅读正文",
        },
      ]}
      readingFocus={{ id: "note-destination", start, end: start + 2 }}
      onReadingNavigate={navigate}
    />,
  );
  const link = container.querySelector<HTMLAnchorElement>(
    "a[data-reading-link]",
  )!;
  expect(link.textContent).toBe("目录条目 …… 12");
  expect(container.querySelector("mark[data-reading-focus]")?.textContent).toBe(
    "原注",
  );
  expect(container.querySelectorAll("a[data-note-jump]")).toHaveLength(2);
  await userEvent.click(link);
  expect(navigate).toHaveBeenCalledWith("#/books/book?chapter=body");
});

test("a source-only destination reports failure without marking another visible word", () => {
  const markdown = "[链接](https://example.test/正文)";
  const start = markdown.indexOf("正文"),
    fail = vi.fn();
  const { container } = render(
    <RenderedMarkdown
      markdown={markdown}
      readingFocus={{ id: "hidden-source", start, end: start + 2 }}
      onReadingFailure={fail}
    />,
  );
  expect(container.querySelector("mark[data-reading-focus]")).toBeNull();
  expect(fail).toHaveBeenLastCalledWith(["hidden-source"]);
});

test("a search range starting inside heading text can span following paragraphs without changing their structure", () => {
  const markdown = "# 标题\n\n正文第一段。\n\n继续正文。\n";
  const baseline = render(<RenderedMarkdown markdown={markdown} />);
  const failed = vi.fn();
  const { container } = render(
    <RenderedMarkdown
      markdown={markdown}
      readingFocus={{
        id: "search-whole-fragment",
        start: 2,
        end: Array.from(markdown).length,
      }}
      onReadingFailure={failed}
    />,
  );
  expect(
    [...container.querySelectorAll("p")].map((node) => node.textContent),
  ).toEqual(["正文第一段。", "继续正文。"]);
  expect(
    container.querySelector("mark[data-reading-focus]")?.textContent?.trim(),
  ).toBe("标题");
  expect(
    [...container.querySelectorAll("p mark[data-reading-focus]")].map(
      (node) => node.textContent,
    ),
  ).toEqual(["正文第一段。", "继续正文。"]);
  const restored = container.firstElementChild!.cloneNode(true) as HTMLElement;
  for (const mark of restored.querySelectorAll("mark[data-reading-focus]"))
    mark.replaceWith(...mark.childNodes);
  restored.normalize();
  expect(restored.outerHTML).toBe(
    baseline.container.firstElementChild!.outerHTML,
  );
  expect(failed).toHaveBeenLastCalledWith([]);
});

test("the actual backend search target renders an exact cross-block highlight", () => {
  const fixture: {
    chapter: components["schemas"]["Chapter"];
    target: ReadingTarget;
  } = JSON.parse(
    readFileSync(
      resolve(process.cwd(), "../tests/fixtures/reading_heading_target.json"),
      "utf8",
    ),
  );
  const href = readingHref(fixture.chapter.book_id, fixture.target);
  const request = requestedReadingTarget(
    new URL(href, "http://localhost").searchParams,
  );
  expect(request).not.toBeNull();
  const range = readingRange(request!, fixture.chapter);
  expect(range).toEqual({ start: 3, end: 17 });
  const failed = vi.fn();
  const baseline = render(
    <RenderedMarkdown markdown={fixture.chapter.reading_text!} />,
  );
  const { container } = render(
    <RenderedMarkdown
      markdown={fixture.chapter.reading_text!}
      readingFocus={{ id: "actual-search-target", ...range! }}
      onReadingFailure={failed}
    />,
  );
  const marks = [...container.querySelectorAll("mark[data-reading-focus]")];
  expect(marks.length).toBeGreaterThan(0);
  expect(
    marks
      .map((node) => node.textContent)
      .join("")
      .trim(),
  ).toBe(baseline.container.textContent?.trim());
  expect(
    container.querySelector("p mark[data-reading-focus]")?.textContent,
  ).toBe("😀正文。后续内容。");
  expect(failed).toHaveBeenLastCalledWith([]);
  const restored = container.firstElementChild!.cloneNode(true) as HTMLElement;
  for (const mark of restored.querySelectorAll("mark[data-reading-focus]"))
    mark.replaceWith(...mark.childNodes);
  restored.normalize();
  expect(restored.outerHTML).toBe(
    baseline.container.firstElementChild!.outerHTML,
  );
});

test("a TOC range containing an existing link falls back atomically", () => {
  const markdown = "前段 [已有链接](https://example.test) 后段";
  const fail = vi.fn();
  const { container } = render(
    <RenderedMarkdown
      markdown={markdown}
      readingLinks={[
        {
          id: "overlap",
          start: 0,
          end: markdown.length,
          href: "#/books/book?chapter=body",
        },
      ]}
      onReadingFailure={fail}
    />,
  );
  expect(container.querySelectorAll("a[data-reading-link]")).toHaveLength(0);
  expect(container.querySelector("a")?.getAttribute("href")).toBe(
    "https://example.test",
  );
  expect(fail).toHaveBeenLastCalledWith(["overlap"]);
});

test("returning to the same location after leaving it focuses again without repeated render scrolling", () => {
  const scroll = vi
    .spyOn(HTMLElement.prototype, "scrollIntoView")
    .mockImplementation(() => {});
  const focus = { id: "same-history-location", start: 0, end: 2 };
  const { rerender } = render(
    <RenderedMarkdown markdown="原文段落" readingFocus={focus} />,
  );
  expect(scroll).toHaveBeenCalledTimes(1);
  rerender(<RenderedMarkdown markdown="别的章节" />);
  rerender(<RenderedMarkdown markdown="原文段落" readingFocus={focus} />);
  expect(scroll).toHaveBeenCalledTimes(2);
  expect(document.activeElement?.getAttribute("data-reading-focus")).toBe(
    focus.id,
  );
  rerender(<RenderedMarkdown markdown="原文段落" readingFocus={focus} />);
  expect(scroll).toHaveBeenCalledTimes(2);
});
