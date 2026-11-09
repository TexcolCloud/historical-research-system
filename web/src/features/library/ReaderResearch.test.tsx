// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { respondWith } from "@/test/responses";
import { research } from "@/features/research/api";
import ReaderPanel from "./ReaderPanel";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("an unchanged footnote source range safely falls back without blaming the source version or directory", async () => {
  const sha = "a".repeat(64);
  const text =
    "# 标题[^*]\n\n正文[^1]。\n\n[^*]: 星注提及张某。\n\n（1）数字注原文。";
  const start = text.indexOf("[^*]:"),
    end = text.indexOf("\n\n（1）");
  const person = text.indexOf("张某");
  const numeric = text.indexOf("（1）");
  const footnotes = [
    {
      id: "star-note",
      label: "*",
      note: { start, end, marker_end: start + 5 },
      references: [
        { start: text.indexOf("[^*]"), end: text.indexOf("[^*]") + 4 },
      ],
    },
    {
      id: "numeric-note",
      label: "1",
      note: { start: numeric, end: text.length, marker_end: numeric + 3 },
      references: [
        { start: text.indexOf("[^1]"), end: text.indexOf("[^1]") + 4 },
      ],
    },
  ];
  const chapter = {
    id: "chapter",
    book_id: "book",
    run_id: "run",
    position: 0,
    title: "标题",
    kind: "article",
    pages: [1, 2],
    codepoints: text.length,
    text,
    reading_text: text,
    text_sha256: sha,
    parts: [],
    reading_segments: [],
    footnotes,
    reading_footnotes: footnotes,
    toc_links: [],
  };
  respondWith((request) =>
    request.url.includes("reading-navigation")
      ? { entries: [], state: "not_detected", version: 0, sha256: "" }
      : request.url.endsWith("/chapters")
        ? [chapter]
        : chapter,
  );
  vi.spyOn(research, "reading").mockResolvedValue({
    chapter_id: "chapter",
    run_id: "run",
    text_sha256: sha,
    source_current: true,
    cards: [],
    annotations: [
      {
        id: "first",
        entity_id: "first-person",
        kind: "person",
        name: "张某",
        start: person,
        end: person + 2,
        quote: "张某",
        text_sha256: sha,
        card_id: "card",
      },
      {
        id: "overlap",
        entity_id: "another-person",
        kind: "person",
        name: "张某",
        start: person,
        end: person + 2,
        quote: "张某",
        text_sha256: sha,
        card_id: "card",
      },
    ],
  });
  const { container } = render(
    <MemoryRouter
      initialEntries={[
        `/books/book?tab=reader&chapter=chapter&run=run&textSha=${sha}&physicalPage=1&start=${start}&end=${end}`,
      ]}
    >
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ReaderPanel bookId="book" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(
    await screen.findByText(
      "此位置暂不能精确对应阅读版。请结合原图核对，未标记其他文字。",
    ),
  ).toBeTruthy();
  expect(
    await screen.findByText(
      "部分关联位置无法安全显示为链接，可从原书目录或“相关史料”继续查阅。",
    ),
  ).toBeTruthy();
  expect(screen.queryByText(/定位依据已更新/)).toBeNull();
  expect(screen.queryByText(/部分目录文字/)).toBeNull();
  expect(container.querySelector("mark[data-reading-focus]")).toBeNull();
  expect(chapter.text).toBe(text);
  await userEvent.click(screen.getByRole("link", { name: "查看脚注 1" }));
  expect(document.activeElement?.id).toContain("numeric-note");
});
test("reading opens an exact person mention and its related card without linking another same-name person", async () => {
  const sha = "a".repeat(64),
    text = "甲见张某。乙见张某。";
  const chapter = {
    id: "chapter",
    book_id: "book",
    run_id: "run",
    position: 0,
    title: "正文",
    kind: "article",
    pages: [1],
    codepoints: text.length,
    text,
    reading_text: text,
    text_sha256: sha,
    parts: [],
    reading_segments: [],
    toc_links: [],
  };
  respondWith((request) =>
    request.url.includes("reading-navigation")
      ? { entries: [], state: "not_detected", version: 0, sha256: "" }
      : request.url.endsWith("/chapters")
        ? [chapter]
        : chapter,
  );
  vi.spyOn(research, "reading").mockImplementation(async () => ({
    chapter_id: "chapter",
    run_id: "run",
    text_sha256: sha,
    source_current: true,
    cards: [
      {
        id: "card",
        book_id: "book",
        run_id: "research",
        article_id: "chapter",
        title: "人物活动记载",
        summary: "保留两处身份差别",
        state: "verified",
        version: 1,
        approval_type: "native",
        tags: [],
        document_type: "",
        source_layer: "",
        article_title: "正文",
        evidence_count: 1,
        item_count: 1,
        entity_count: 1,
        event_count: 0,
        relation_count: 0,
        cross_relation_count: 0,
        coverage_count: 0,
        card_only: false,
        processing_mode: "legacy_research" as const,
        source_current: true,
        needs_update: false,
        source_status: "current",
        research_current: true,
        superseded: false,
      },
    ],
    annotations: [
      {
        id: "mention",
        entity_id: "canonical-person",
        kind: "person",
        name: "张某",
        start: 2,
        end: 4,
        quote: "张某",
        text_sha256: sha,
        card_id: "card",
      },
    ],
  }));
  render(
    <MemoryRouter initialEntries={["/books/book?tab=reader&chapter=chapter"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ReaderPanel bookId="book" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const mention = await screen.findByRole("link", { name: "查看人物：张某" });
  expect(screen.getAllByRole("link", { name: "查看人物：张某" })).toHaveLength(
    1,
  );
  expect(mention.getAttribute("href")).toContain("entity=canonical-person");
  await userEvent.click(screen.getByRole("button", { name: "相关史料" }));
  const card = screen.getByRole("link", { name: "人物活动记载" });
  expect(card.getAttribute("href")).toContain("card=card");
  expect(card.getAttribute("href")).toContain("returnTo=");
});
