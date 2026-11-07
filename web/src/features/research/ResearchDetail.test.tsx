// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ResearchDetail from "./ResearchDetail";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const card = {
  id: "last-card",
  book_id: "book",
  run_id: "research-run",
  article_id: "article",
  title: "最后一张卡",
  summary: "模型提要",
  version: 1,
  state: "pending",
  items: [],
  evidence: [
    {
      id: "ev",
      article_id: "article",
      start: 0,
      end: 3,
      quote: "原文证据",
      pages: [9],
      footnote_ids: [],
      reading_target: {
        chapter_id: "article",
        run_id: "source",
        physical_page: 9,
        start: 0,
        end: 3,
        text_sha256: "a".repeat(64),
        image_sha256: "b".repeat(64),
        anchor: "原文证据",
      },
    },
  ],
  mentions: [],
  events: [],
  relations: [],
  tags: [],
  limitations: ["该记载仅代表作者所见"],
  alternatives: [],
  questions: [],
};

test("accepting the final pending card retains its evidence and refreshes the task and book state", async () => {
  let decision: unknown;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/decision")) {
        decision = JSON.parse(String(init?.body));
        return Response.json({
          ...card,
          version: 2,
          state: "human_accepted",
          approval_type: "user",
        });
      }
      return Response.json(card);
    }),
  );
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  query.setQueryData(["research", "book", "book"], { run: "pending" });
  query.setQueryData(["research", "run", "research-run"], { state: "pending" });
  render(
    <MemoryRouter initialEntries={["/books/book?tab=research&card=last-card"]}>
      <QueryClientProvider client={query}>
        <ResearchDetail />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "确认采用此卡" }),
  );
  await screen.findByText("已人工确认");
  expect(decision).toEqual({ action: "accept", expected_version: 1 });
  expect(screen.getByText("原文证据")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: /阅读原文.*9/ }).getAttribute("href"),
  ).toContain("physicalPage=9");
  expect(query.getQueryState(["research", "book", "book"])?.isInvalidated).toBe(
    true,
  );
  expect(
    query.getQueryState(["research", "run", "research-run"])?.isInvalidated,
  ).toBe(true);
  expect(screen.queryByRole("button", { name: "确认采用此卡" })).toBeNull();
});

test("a stale card read cannot replace a saved human decision", async () => {
  let reads = 0;
  let resolveLate!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/decision"))
        return Response.json({ ...card, state: "human_accepted", version: 2 });
      if (++reads === 1) return Response.json(card);
      return new Promise<Response>((resolve) => {
        resolveLate = resolve;
      });
    }),
  );
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MemoryRouter initialEntries={["/research?card=last-card"]}>
      <QueryClientProvider client={query}>
        <ResearchDetail />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByRole("button", { name: "确认采用此卡" });
  act(() => {
    void query.refetchQueries({ queryKey: ["research", "card", "last-card"] });
  });
  await vi.waitFor(() => expect(reads).toBe(2));
  await userEvent.click(screen.getByRole("button", { name: "确认采用此卡" }));
  await screen.findByText("已人工确认");
  await act(async () => {
    resolveLate(Response.json(card));
  });
  expect(screen.queryByRole("button", { name: "确认采用此卡" })).toBeNull();
  expect(query.getQueryData(["research", "card", "last-card"])).toMatchObject({
    state: "human_accepted",
    version: 2,
  });
});

test("linked editorial footnotes retain their text and their own exact source location", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        ...card,
        evidence: [
          {
            ...card.evidence[0],
            footnote_ids: ["note-8"],
            footnotes: [
              {
                id: "note-8",
                label: "①",
                text: "① 编者注：此处年份存疑。",
                reading_target: {
                  ...card.evidence[0].reading_target,
                  start: 8,
                  end: 22,
                  anchor: "① 编者注：此处年份存疑。",
                },
              },
            ],
          },
        ],
      }),
    ),
  );
  render(
    <MemoryRouter initialEntries={["/research?card=last-card"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchDetail />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(await screen.findByText("关联注释 · 1 项"));
  expect(screen.getByText("① 编者注：此处年份存疑。")).toBeTruthy();
  const link = screen.getByRole("link", { name: "阅读注释 ①" });
  const href = new URL(link.getAttribute("href")!, "http://local");
  expect(href.searchParams.get("start")).toBe("8");
  expect(href.searchParams.get("end")).toBe("22");
  expect(href.searchParams.get("textSha")).toBe("a".repeat(64));
});

test("a direct link to an old-publication card labels its history and cannot accept it as current research", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        ...card,
        source_current: false,
        needs_update: true,
        source_status: "stale",
      }),
    ),
  );
  render(
    <MemoryRouter initialEntries={["/research?card=last-card"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchDetail />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("原文已换版 · 卡片待更新");
  expect(screen.queryByRole("button", { name: "确认采用此卡" })).toBeNull();
  expect(screen.queryByRole("button", { name: "修订提要" })).toBeNull();
  expect(
    screen.getByRole("link", { name: "导出历史卡片" }).getAttribute("href"),
  ).toContain("/last-card/export");
  const source = screen.getByRole("link", { name: /阅读历史原文.*9/ });
  const href = new URL(source.getAttribute("href")!, "http://local");
  expect(href.searchParams.get("textSha")).toBe("a".repeat(64));
  expect(href.searchParams.get("run")).toBe("source");
});

test("a deleted book's research deep link gives a clear return path without offering to approve or repeatedly reload", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ detail: "Record missing" }, { status: 404 }),
    ),
  );
  render(
    <MemoryRouter initialEntries={["/research?card=deleted-card"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchDetail />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("研究记录不存在，所属书籍可能已删除。");
  expect(
    screen.getByRole("link", { name: "返回书籍" }).getAttribute("href"),
  ).toBe("/");
  expect(screen.queryByRole("button", { name: "确认采用此卡" })).toBeNull();
  expect(screen.queryByRole("button", { name: "重新读取史料卡" })).toBeNull();
});
