// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import NetworkEvidence from "./NetworkEvidence";
import type { ResearchTimelineItem } from "./api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function renderEvidence(item: ResearchTimelineItem) {
  return render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <NetworkEvidence
          selection={{ kind: "event", id: item.id }}
          item={item}
          bookId="book"
          onCenter={vi.fn()}
          onTimeline={vi.fn()}
          onClear={vi.fn()}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
const item: ResearchTimelineItem = {
  id: "source-event",
  entity_id: "event",
  item_kind: "event",
  card_id: "card",
  card_version: 1,
  book_id: "book",
  run_id: "run",
  name: "旧版拟议事项",
  description: "事项范围取自旧版",
  date: {
    text: "1948年",
    normalized: "1948",
    certainty: "stated",
    evidence_ids: ["proof"],
  },
  date_basis: "event",
  date_precision: "year",
  undated: false,
  event_status: "planned",
  status_note: "计划尚未实行",
  evidence_ids: ["proof"],
  evidence_count: 1,
  source_current: true,
  needs_update: false,
};

test("a newer card cannot supply reused evidence IDs to a cached older event", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        id: "card",
        version: 2,
        title: "新版卡片",
        book_id: "book",
        summary: "新版摘要",
        state: "verified",
        evidence: [
          {
            id: "proof",
            article_id: "article",
            start: 0,
            end: 4,
            quote: "新版不同原文",
            pages: [2],
            footnote_ids: [],
          },
        ],
      }),
    ),
  );
  renderEvidence(item);
  expect(await screen.findByText(/来源版本已变化/)).toBeTruthy();
  expect(screen.queryByText("新版不同原文")).toBeNull();
  expect(screen.queryByRole("button", { name: "对照原件" })).toBeNull();
});

test("book-scoped entity evidence retains the selected mention's card and original book", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        id: "person",
        name: "同一实体",
        kind: "person",
        book_id: "book-a",
        card_id: "card-a",
        identity_basis: "甲书特有解释",
        evidence: [],
        source_mentions: [
          {
            id: "mention-b",
            card_id: "card-b",
            book_id: "book-b",
            identity_basis: "乙书记载",
            evidence: [
              {
                id: "proof",
                article_id: "article-b",
                start: 0,
                end: 4,
                quote: "乙书原文",
                pages: [9],
                reading_target: {
                  chapter_id: "article-b",
                  run_id: "run-b",
                  physical_page: 9,
                  start: 0,
                  end: 4,
                  anchor: "乙书原文",
                  text_sha256: "a".repeat(64),
                  image_sha256: "b".repeat(64),
                },
              },
            ],
          },
        ],
      }),
    ),
  );
  render(
    <MemoryRouter
      initialEntries={["/research?view=network&entity=person&book_id=book-b"]}
    >
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <NetworkEvidence
          selection={{ kind: "entity", id: "person" }}
          bookId="book-b"
          onCenter={vi.fn()}
          onTimeline={vi.fn()}
          onClear={vi.fn()}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByText("乙书记载")).toBeTruthy();
  expect(screen.queryByText("甲书特有解释")).toBeNull();
  expect(
    screen.getByRole("link", { name: "查看出处史料卡" }).getAttribute("href"),
  ).toContain("card=card-b");
  expect(
    screen.getByRole("link", { name: /阅读原文/ }).getAttribute("href"),
  ).toContain("/books/book-b?");
});

test("a relation's document date and planned status cannot look like an occurrence date", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        id: "edge",
        source_id: "person",
        target_id: "event",
        card_id: "card",
        book_id: "book",
        predicate: "提出行动计划",
        reason: "有条件的行动安排",
        date: {
          text: "1948年1月10日",
          normalized: "1948-01-10",
          certainty: "stated",
        },
        date_basis: "formation",
        event_status: "planned",
        status_note: "材料没有证明已经实行",
        evidence: [],
      }),
    ),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <NetworkEvidence
          selection={{ kind: "relation", id: "edge" }}
          bookId="book"
          onCenter={vi.fn()}
          onTimeline={vi.fn()}
          onClear={vi.fn()}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByText(/文献形成日期：1948年1月10日/)).toBeTruthy();
  expect(screen.getByText("计划或拟议事项")).toBeTruthy();
  expect(screen.getByText("该日期不代表事项发生日期。")).toBeTruthy();
});
