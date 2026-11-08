// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ResearchSearch from "./ResearchSearch";
import * as libraryApi from "@/client/api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("cross-book search keeps the selected book filter and opens an exact source with a restorable search URL", async () => {
  const requests: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://local");
      requests.push(url);
      return Response.json({
        query: "运输",
        available: true,
        backend: "lexical",
        cards: [],
        entities: [],
        hits: [
          {
            id: "hit",
            book_id: "book-b",
            title: "运输记录",
            text: "甲负责运输。",
            pages: [8],
            reading_target: {
              chapter_id: "chapter-b",
              run_id: "source-run",
              physical_page: 8,
              start: 3,
              end: 8,
              text_sha256: "a".repeat(64),
              image_sha256: "b".repeat(64),
              anchor: "负责运输。",
            },
          },
        ],
      });
    }),
  );
  render(
    <MemoryRouter initialEntries={["/research?q=运输&book_id=book-b"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchSearch />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const link = await screen.findByRole("link", { name: "阅读命中原文" });
  const href = new URL(link.getAttribute("href")!, "http://local");
  expect(href.pathname).toBe("/books/book-b");
  expect(href.searchParams.get("start")).toBe("3");
  expect(href.searchParams.get("physicalPage")).toBe("8");
  expect(href.searchParams.get("textSha")).toBe("a".repeat(64));
  expect(href.searchParams.get("returnTo")).toBe(
    "/research?q=运输&book_id=book-b",
  );
  expect(requests[0].searchParams.get("book_id")).toBe("book-b");
  await userEvent.click(screen.getByRole("button", { name: "改查全库" }));
  await vi.waitFor(() =>
    expect(requests.at(-1)?.searchParams.has("book_id")).toBe(false),
  );
  expect(requests.at(-1)?.searchParams.get("q")).toBe("运输");
});

test("a scope without indexed publications explains the missing source corpus instead of claiming an unsuccessful content search", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        query: "运输",
        available: true,
        backend: "hybrid",
        evidence_status: "no_published_sources",
        cards: [],
        entities: [],
        hits: [],
      }),
    ),
  );
  render(
    <MemoryRouter initialEntries={["/research?q=运输"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchSearch />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("当前范围尚无已发布并完成索引的原文，请先完成入库。");
  expect(
    screen.queryByText("没有找到相关内容。可缩短关键词后重试。"),
  ).toBeNull();
  expect(
    screen.getByRole("link", { name: "查看书籍处理进展" }).getAttribute("href"),
  ).toBe("/");
});

test("explicit same-query search refreshes newly finished cards once and a changed query makes only its own request", async () => {
  let cardsReady = false;
  const requests: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://local");
      requests.push(url);
      return Response.json({
        query: url.searchParams.get("q"),
        available: true,
        backend: "hybrid",
        evidence_status: "candidates",
        hits: [],
        entities: [],
        cards: cardsReady
          ? [
              {
                id: "new-card",
                book_id: "book",
                title: "新制史料卡",
                summary: "新保存的研究结果",
              },
            ]
          : [],
      });
    }),
  );
  render(
    <MemoryRouter
      initialEntries={[
        "/research?q=新四军&book_id=book&card=prior-card&entity=prior-entity",
      ]}
    >
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchSearch />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("没有找到相关内容。可缩短关键词后重试。");
  cardsReady = true;
  await userEvent.click(screen.getByRole("button", { name: "查找" }));
  await screen.findByRole("button", { name: "新制史料卡" });
  expect(requests).toHaveLength(2);
  expect(requests[1].searchParams.get("q")).toBe("新四军");
  expect(requests[1].searchParams.get("book_id")).toBe("book");
  const input = screen.getByRole("textbox", { name: "检索全库原文与史料卡" });
  await userEvent.clear(input);
  await userEvent.type(input, "补给");
  await userEvent.click(screen.getByRole("button", { name: "查找" }));
  await vi.waitFor(() => expect(requests).toHaveLength(3));
  expect(requests[2].searchParams.get("q")).toBe("补给");
});

test("choosing two books submits one joint search with both source filters", async () => {
  const searches: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://local");
      if (url.pathname === "/api/v2/books")
        return Response.json([
          { id: "book-a", title: "甲书", state: "ready" },
          { id: "book-b", title: "乙书", state: "ready" },
        ]);
      searches.push(url);
      return Response.json({
        query: "运输",
        available: true,
        backend: "hybrid",
        hits: [],
        cards: [],
        entities: [],
      });
    }),
  );
  vi.spyOn(libraryApi, "readBooks").mockImplementation(async () =>
    (await fetch("/api/v2/books")).json(),
  );
  render(
    <MemoryRouter initialEntries={["/research?q=运输"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchSearch />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(screen.getByRole("button", { name: "选择书籍" }));
  await userEvent.click(await screen.findByRole("checkbox", { name: "甲书" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "乙书" }));
  const before = searches.length;
  await userEvent.click(screen.getByRole("button", { name: "应用书籍范围" }));
  await vi.waitFor(() => expect(searches).toHaveLength(before + 1));
  expect(searches.at(-1)?.searchParams.getAll("book_ids")).toEqual([
    "book-a",
    "book-b",
  ]);
});
