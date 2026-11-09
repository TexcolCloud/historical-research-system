// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import BookResearch from "./BookResearch";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function HistoryBack() {
  const navigate = useNavigate();
  return <button onClick={() => navigate(-1)}>返回此前卡片</button>;
}
const book = {
  id: "book",
  title: "材料",
  state: "ready",
  created_at: "2026-11-08",
  revision: 1,
  pending_count: 0,
  navigation_pending_count: 0,
  revision_pending: false,
};
const run = {
  run_id: "new-run",
  book_id: "book",
  source_run_id: "source",
  publication_sha256: "a".repeat(64),
  input_hash: "b".repeat(64),
  version: 1,
  state: "running",
  counts: { articles_total: 1, articles_completed: 0, articles_pending: 0 },
  card_ids: [],
  tasks: [],
  card_only: true,
};

test("indexed books wait for automatic card creation without exposing a start action or sending a mutation", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) =>
    String(input).includes("/cards?")
      ? Response.json({ items: [], total: 0, offset: 0, limit: 50 })
      : Response.json({
          book_id: "book",
          runs: [],
          indexed: true,
          source_version: "a".repeat(64),
          cards_count: 0,
          entities_count: 0,
        }),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <BookResearch book={book} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("史料卡待自动制作");
  expect(screen.queryByRole("button", { name: /开始制作/ })).toBeNull();
  expect(
    fetch.mock.calls.every((call) => call.length === 1 || !call[1]?.method),
  ).toBe(true);
});

test("the catalogue identifies one complete document by stable display number, author, date and physical pages without expanding its themes", async () => {
  const entry = {
    id: "document-full",
    display_id: "CARD-ABCDEF123456",
    title: "完整报告题名",
    authors:
      "甲作者、乙作者、丙作者、丁作者、戊作者、己作者、庚作者、辛作者、壬作者",
    formation_date: {
      text: "1939年5月",
      certainty: "stated",
      evidence_ids: [],
    },
    pages: [3, 4, 5],
    printed_pages: "10—12",
    summary: "这个摘要不应占据文献清单",
    tags: ["不显示的主题标签"],
    state: "verified",
    run_id: "run",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      String(input).includes("/cards?")
        ? Response.json({ items: [entry], total: 1, offset: 0, limit: 50 })
        : Response.json({
            book_id: "book",
            runs: [],
            indexed: true,
            source_version: "a".repeat(64),
            cards_count: 1,
            entities_count: 0,
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
        <BookResearch book={book} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("完整报告题名");
  expect(screen.getByText("卡片编号 CARD-ABCDEF123456")).toBeTruthy();
  expect(screen.getByText(entry.authors).getAttribute("title")).toBe(
    entry.authors,
  );
  expect(screen.getByText(entry.title).getAttribute("title")).toBe(entry.title);
  expect(screen.getByText("形成：1939年5月")).toBeTruthy();
  expect(screen.getByText("PDF 物理页 3–5 · 原书页 10—12")).toBeTruthy();
  expect(screen.getByText("模型核验通过")).toBeTruthy();
  expect(screen.queryByText(entry.summary)).toBeNull();
  expect(screen.queryByText(entry.tags[0])).toBeNull();
});

test("legacy research cards remain readable without offering a new research upgrade", async () => {
  const old = {
    ...run,
    card_only: false,
    state: "completed",
    run_id: "legacy-v4",
  };
  const request = vi.fn(
    async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/cards?"))
        return Response.json({ items: [], total: 0, offset: 0, limit: 50 });
      if (url.includes("/runs/")) return Response.json(old);
      return Response.json({
        book_id: "book",
        runs: [old],
        indexed: true,
        source_version: old.publication_sha256,
        cards_count: 0,
        entities_count: 0,
        needs_upgrade: true,
        research_policy_version: 4,
        required_research_policy_version: 6,
      });
    },
  );
  vi.stubGlobal("fetch", request);
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <BookResearch book={book} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("旧流程记录 · 保留原卡与审核依据");
  expect(screen.queryByRole("button", { name: "补充完整研究信息" })).toBeNull();
  expect(request.mock.calls.every((call) => !call[1]?.method)).toBe(true);
});

test("a new research revision has its own progress while prior completed cards remain readable", async () => {
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requests.push(url);
      if (url.includes("/runs/")) return Response.json(run);
      if (url.includes("/cards?"))
        return Response.json({
          items: [
            {
              id: "old-card",
              title: "此前完成的卡片",
              summary: "保留提要与证据",
              state: "verified",
              run_id: "old-run",
              tags: [],
            },
          ],
          total: 1,
          offset: 0,
          limit: 50,
        });
      return Response.json({
        book_id: "book",
        runs: [run, { ...run, run_id: "old-run", state: "completed" }],
        indexed: true,
        source_version: run.publication_sha256,
        cards_count: 1,
        entities_count: 0,
        stale_cards_count: 0,
        needs_update: false,
        source_revision_pending: false,
      });
    }),
  );
  render(
    <MemoryRouter initialEntries={["/books/book?tab=research"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <BookResearch book={book} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("新卡片修订正在处理，清单保留此前完成的卡片。");
  expect(screen.getByText("此前完成的卡片")).toBeTruthy();
  expect(screen.getByText("史料卡正在制作")).toBeTruthy();
  expect(requests.some((url) => url.endsWith("/runs/old-run"))).toBe(false);
});

test("new source publication reports historical cards pending update instead of showing the old completed task as current", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/runs/"))
        return Response.json({ ...run, run_id: "old-run", state: "completed" });
      if (String(input).includes("/cards?"))
        return Response.json({ items: [], total: 0, offset: 0, limit: 50 });
      return Response.json({
        book_id: "book",
        runs: [{ ...run, run_id: "old-run", state: "completed" }],
        indexed: true,
        source_version: "c".repeat(64),
        cards_count: 0,
        entities_count: 0,
        stale_cards_count: 3,
        needs_update: true,
        source_revision_pending: false,
      });
    }),
  );
  render(
    <MemoryRouter initialEntries={["/books/book?tab=research"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <BookResearch book={book} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("3 张历史卡片待更新");
  expect(screen.getByText("史料卡待自动更新")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "更新史料卡" })).toBeNull();
  expect(screen.queryByText("研究任务已完成")).toBeNull();
});

test("outdated evidence waits for an automatic revision without creating one from the page", async () => {
  const request = vi.fn(
    async (input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input).includes("/cards?"))
        return Response.json({ items: [], total: 0, offset: 0, limit: 50 });
      return Response.json({
        book_id: "book",
        runs: [{ ...run, state: "completed" }],
        indexed: true,
        source_version: run.publication_sha256,
        cards_count: 0,
        entities_count: 0,
        stale_cards_count: 1,
        needs_update: true,
        source_revision_pending: false,
      });
    },
  );
  vi.stubGlobal("fetch", request);
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <BookResearch book={book} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("史料卡待自动更新");
  expect(screen.queryByRole("button", { name: "更新史料卡" })).toBeNull();
  expect(request.mock.calls.every((call) => !call[1]?.method)).toBe(true);
});

test("excluding the last card on a later page returns to a valid page of remaining cards", async () => {
  let excluded = false;
  const lastCard = {
    id: "last-card",
    book_id: "book",
    run_id: "new-run",
    article_id: "article",
    title: "最后一张卡",
    summary: "待核验提要",
    version: 1,
    state: "pending",
    tags: [],
    items: [],
    evidence: [
      {
        id: "ev",
        article_id: "article",
        start: 0,
        end: 2,
        quote: "原文",
        pages: [1],
        footnote_ids: [],
      },
    ],
    mentions: [],
    events: [],
    relations: [],
    limitations: [],
    alternatives: [],
    questions: [],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/decision")) {
        excluded = true;
        return Response.json({ ...lastCard, state: "excluded", version: 2 });
      }
      if (url.includes("/cards/last-card"))
        return Response.json(
          excluded ? { ...lastCard, state: "excluded", version: 2 } : lastCard,
        );
      if (url.includes("/cards?")) {
        const offset = Number(
          new URL(url, "http://local").searchParams.get("offset"),
        );
        return Response.json({
          items:
            offset === 0
              ? [
                  {
                    ...lastCard,
                    id: "remaining",
                    title: "仍可阅读的卡片",
                    state: "verified",
                  },
                ]
              : excluded
                ? []
                : [lastCard],
          total: excluded ? 50 : 51,
          offset,
          limit: 50,
        });
      }
      const current = {
        ...run,
        state: excluded ? "completed" : "pending",
        card_ids: ["last-card"],
      };
      if (url.includes("/runs/")) return Response.json(current);
      return Response.json({
        book_id: "book",
        runs: [current],
        indexed: true,
        source_version: run.publication_sha256,
        cards_count: excluded ? 50 : 51,
        entities_count: 0,
        stale_cards_count: 0,
        needs_update: false,
        source_revision_pending: false,
      });
    }),
  );
  render(
    <MemoryRouter
      initialEntries={[
        "/books/book?tab=research&cardsOffset=50&card=last-card",
      ]}
    >
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <BookResearch book={book} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "核对本卡" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "排除此卡" }),
  );
  await screen.findByText("仍可阅读的卡片");
  expect(
    screen.queryByText("尚无卡片。任务生成的结果会保存在这里。"),
  ).toBeNull();
});

test("the new pending revision can be opened and accepted while the old completed card remains readable", async () => {
  let accepted = false;
  let decisionPath = "";
  const draft = {
    id: "new-card",
    book_id: "book",
    run_id: "new-run",
    article_id: "article",
    title: "新修订待核验卡",
    summary: "新提要",
    version: 1,
    state: "pending",
    tags: [],
    items: [],
    evidence: [
      {
        id: "ev",
        article_id: "article",
        start: 0,
        end: 2,
        quote: "原文",
        pages: [1],
        footnote_ids: [],
      },
    ],
    mentions: [],
    events: [],
    relations: [],
    limitations: [],
    alternatives: [],
    questions: [],
  };
  const old = {
    ...draft,
    id: "old-card",
    run_id: "old-run",
    title: "此前已完成卡片",
    state: "verified",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const currentRun = {
        ...run,
        state: accepted ? "completed" : "pending",
        card_ids: ["new-card"],
      };
      if (url.endsWith("/decision")) {
        accepted = true;
        decisionPath = url;
        return Response.json({ ...draft, state: "human_accepted", version: 2 });
      }
      if (url.endsWith("/cards/new-card"))
        return Response.json(
          accepted ? { ...draft, state: "human_accepted", version: 2 } : draft,
        );
      if (url.endsWith("/cards/old-card"))
        return Response.json(
          accepted
            ? { ...old, research_current: false, superseded: true }
            : old,
        );
      if (url.includes("/cards?"))
        return Response.json({
          items: accepted ? [{ ...draft, state: "human_accepted" }] : [old],
          total: 1,
          offset: 0,
          limit: 50,
        });
      if (url.includes("/runs/")) return Response.json(currentRun);
      return Response.json({
        book_id: "book",
        runs: [currentRun, { ...run, run_id: "old-run", state: "completed" }],
        indexed: true,
        source_version: run.publication_sha256,
        cards_count: 1,
        entities_count: 0,
        stale_cards_count: 0,
        needs_update: false,
        source_revision_pending: false,
      });
    }),
  );
  render(
    <MemoryRouter initialEntries={["/books/book?tab=research"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <BookResearch book={book} />
        <HistoryBack />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("此前已完成卡片");
  await userEvent.click(
    screen.getByRole("button", { name: /模型核验通过.*此前已完成卡片/ }),
  );
  await screen.findByRole("heading", { name: "此前已完成卡片" });
  await userEvent.click(screen.getByText("查看处理详情"));
  await userEvent.click(
    await screen.findByRole("button", { name: "查看本轮卡片 1" }),
  );
  await screen.findByRole("heading", { name: "新修订待核验卡" });
  await userEvent.click(screen.getByRole("button", { name: "核对本卡" }));
  await userEvent.click(screen.getByRole("button", { name: "确认采用此卡" }));
  await screen.findByText("你的决定已保存，原有证据与版本继续保留。");
  expect(decisionPath).toBe("/api/v2/research/cards/new-card/decision");
  await userEvent.click(screen.getByRole("button", { name: "返回此前卡片" }));
  await screen.findByText("研究卡已修订 · 历史版本");
});
