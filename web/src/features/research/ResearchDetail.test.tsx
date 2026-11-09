// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ResearchDetail from "./ResearchDetail";
import * as libraryApi from "@/client/api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("entity relationships translate system predicates while preserving the source's natural-language relation", async () => {
  const relation = {
    source_id: "person",
    target_id: "other",
    role: "association",
    card_id: "card",
    card_version: 1,
    book_id: "book",
    run_id: "run",
    evidence: [],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        id: "person",
        name: "陈毅",
        kind: "person",
        card_id: "card",
        book_id: "book",
        run_id: "run",
        state: "verified",
        evidence: [],
        relationships: [
          {
            ...relation,
            id: "system",
            kind: "mentioned_in",
            predicate: "mentioned_in",
            reason: "原文中的来源提及",
          },
          {
            ...relation,
            id: "authored",
            kind: "association",
            predicate: "曾致函请求拨款",
            reason: "按原文保留谓词",
          },
        ],
      }),
    ),
  );
  render(
    <MemoryRouter initialEntries={["/research?entity=person"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchDetail />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByText("见于材料")).toBeTruthy();
  expect(screen.queryByText("mentioned_in")).toBeNull();
  expect(screen.getByText("曾致函请求拨款")).toBeTruthy();
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

test.each([true, false])(
  "source cards retain original viewpoints and documents while old research remains readable: card_only=%s",
  async (cardOnly) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          ...card,
          card_only: cardOnly,
          processing_mode: cardOnly ? "source_cards" : "legacy_research",
          items: [
            {
              id: "view",
              kind: "argument",
              title: "原作者的判断",
              text: "作者认为组织工作尚待巩固",
              evidence_ids: ["ev"],
              epistemic_state: "stated",
              attribution: "原作者",
              limitations: [],
            },
            {
              id: "use",
              kind: "paper_use",
              title: "旧自动写作用途",
              text: "旧流程保留的自动建议",
              evidence_ids: [],
            },
          ],
          mentions: [
            {
              id: "doc",
              kind: "document",
              name: "某文件",
              canonical_id: "canonical-doc",
              evidence_ids: ["ev"],
            },
          ],
          alternatives: ["旧自动解释"],
          questions: ["旧研究问题"],
          research_plan: [
            {
              id: "plan",
              question: "旧补证计划",
              evaluation: "旧查证过程",
              evidence_ids: [],
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
    await screen.findByText("作者认为组织工作尚待巩固");
    expect(screen.getByRole("button", { name: "某文件" })).toBeTruthy();
    expect(screen.getByText("· 文献")).toBeTruthy();
    expect(screen.getByText(cardOnly ? /原文观点/ : /^论证/)).toBeTruthy();
    expect(Boolean(screen.queryByText("旧自动写作用途"))).toBe(!cardOnly);
    expect(Boolean(screen.queryByText("旧研究问题"))).toBe(!cardOnly);
    expect(Boolean(screen.queryByText("旧补证计划"))).toBe(!cardOnly);
    expect(screen.queryByRole("button", { name: "保存到研究问题" })).toBeNull();
  },
);

test("a card citation focuses its own source even when another relationship packet reuses the local evidence id", async () => {
  const scrolled: string[] = [];
  vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(function (
    this: Element,
  ) {
    scrolled.push(this.textContent ?? "");
  });
  vi.spyOn(libraryApi, "readOriginalPage").mockResolvedValue(
    "/retained-source.png",
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        ...card,
        items: [
          {
            id: "item",
            kind: "evidence",
            title: "本卡论据",
            text: "本卡解释",
            evidence_ids: ["ev"],
            epistemic_state: "stated",
            attribution: "",
            limitations: [],
          },
        ],
        derived_relations: {
          truncated: false,
          items: [
            {
              id: "edge",
              source_id: "first",
              target_id: "second",
              card_id: "other-packet",
              card_version: 1,
              kind: "reply_to",
              predicate: "复函",
              reason: "关系补证",
              role: "association",
              book_id: "book",
              run_id: "run",
              evidence: [
                { ...card.evidence[0], quote: "其他证据包中的同号引文" },
              ],
            },
          ],
        },
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
  await userEvent.click(await screen.findByRole("button", { name: "证据 1" }));
  expect(scrolled).toHaveLength(1);
  expect(scrolled[0]).toContain("原文证据");
  expect(scrolled[0]).not.toContain("其他证据包中的同号引文");
  const ids = [
    ...document.querySelectorAll(".research-evidence > article"),
  ].map((node) => node.id);
  expect(new Set(ids).size).toBe(ids.length);
});

function CurrentLocation() {
  const location = useLocation();
  return (
    <output aria-label="当前页面">{location.pathname + location.search}</output>
  );
}

test("saving a pending card opens the next identity task rather than returning to the already resolved card", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/decision"))
        return Response.json({ ...card, state: "human_accepted", version: 2 });
      if (url.includes("/pending?"))
        return Response.json({
          items: [
            { id: "old", kind: "card", card_id: card.id, title: card.title },
            {
              id: "identity-2",
              kind: "identity",
              card_id: "next-card",
              entity_id: "person",
              target_id: "possible-person",
              title: "同名身份",
            },
          ],
          total: 2,
          offset: 0,
          limit: 50,
        });
      if (url.includes("/entities/"))
        return Response.json({
          id: "person",
          name: "张某",
          kind: "person",
          card_id: "next-card",
          evidence: [],
          mentions: [],
        });
      return Response.json(card);
    }),
  );
  render(
    <MemoryRouter
      initialEntries={[
        "/research?from=pending&researchTask=old&card=last-card",
      ]}
    >
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchDetail />
        <CurrentLocation />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "确认采用此卡" }),
  );
  await screen.findByRole("heading", { name: "张某" });
  expect(screen.getByLabelText("当前页面").textContent).toContain(
    "entity=person",
  );
  expect(screen.getByLabelText("当前页面").textContent).toContain(
    "compare=possible-person",
  );
  expect(screen.getByLabelText("当前页面").textContent).not.toContain(
    "card=last-card",
  );
});

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
