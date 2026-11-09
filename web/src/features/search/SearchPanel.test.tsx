// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { respondWith } from "@/test/responses.ts";
import { client } from "@/client/api";
import SearchPanel from "@/features/search/SearchPanel.tsx";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("a pending new query never presents the previous query's empty result as its answer", async () => {
  let resolve!: (response: Response) => void;
  const next = new Promise<Response>((done) => {
    resolve = done;
  });
  const middleware = {
    onRequest: async ({ request }: { request: Request }) => {
      const url = new URL(request.url);
      if (url.pathname.endsWith("reading-navigation"))
        return Response.json({
          entries: [],
          state: "available",
          version: 1,
          sha256: "nav",
        });
      return url.searchParams.get("q") === "新查询" ? next : Response.json([]);
    },
  };
  client.use(middleware);
  try {
    render(
      <MemoryRouter initialEntries={["/books/book?q=旧查询&semantic=false"]}>
        <QueryClientProvider client={new QueryClient()}>
          <SearchPanel bookId="book" />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    await screen.findByText(/^未找到相关内容/);
    await userEvent.clear(screen.getByLabelText("检索本书正文"));
    await userEvent.type(screen.getByLabelText("检索本书正文"), "新查询");
    await userEvent.click(screen.getByRole("button", { name: "查找" }));
    await screen.findByText("正在按原文查找…");
    const staleEmpty = screen.queryByText(/^未找到相关内容/);
    resolve(
      Response.json([
        {
          id: "new-hit",
          title: "新来源",
          text: "新查询的原文",
          pages: [1],
          book_id: "book",
          chapter_id: "chapter",
          context: [],
        },
      ]),
    );
    await screen.findByText("新查询的原文");
    expect(staleEmpty).toBeNull();
  } finally {
    client.eject(middleware);
  }
});

test("search uses precise body targets and explicit directory scopes, with a restorable URL", async () => {
  let requested = "";
  const target = {
    chapter_id: "body",
    run_id: "run",
    physical_page: 8,
    start: 12,
    end: 17,
    text_sha256: "a".repeat(64),
    anchor: "登记120",
    image_sha256: "",
  };
  respondWith((request) => {
    if (request.url.includes("reading-navigation"))
      return {
        entries: [
          {
            id: "toc-a",
            title: "运输",
            level: 1,
            path: ["运输"],
            reading_target: target,
          },
        ],
        state: "available",
        version: 1,
        sha256: "nav",
      };
    requested = request.url;
    return [
      {
        id: "hit",
        book_id: "book",
        run_id: "run",
        chapter_id: "directory",
        title: "目录",
        text: "运输 3",
        pages: [2],
        start: 0,
        end: 5,
        score: 1,
        doc_kind: "navigation",
        reading_target: target,
        directory_links: [],
        context: [],
      },
    ];
  });
  function Location() {
    return <output aria-label="current URL">{useLocation().search}</output>;
  }
  render(
    <MemoryRouter
      initialEntries={[
        "/books/book?tab=reader&section=search&q=运输&semantic=false&toc_entry_id=toc-a",
      ]}
    >
      <QueryClientProvider client={new QueryClient()}>
        <SearchPanel bookId="book" />
        <Location />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("运输 3");
  expect(new URL(requested).searchParams.get("toc_entry_id")).toBe("toc-a");
  expect(screen.getByLabelText("检索本书正文")).toHaveProperty("value", "运输");
  const link = screen.getByRole("link", { name: "阅读对应正文" });
  const href = new URL(link.getAttribute("href")!, "http://local");
  expect(href.searchParams.get("chapter")).toBe("body");
  expect(href.searchParams.get("start")).toBe("12");
  expect(href.searchParams.get("textSha")).toBe("a".repeat(64));
  expect(href.searchParams.get("returnTo")).toContain("toc_entry_id=toc-a");
  await userEvent.click(screen.getByText("检索选项"));
  await userEvent.selectOptions(screen.getByLabelText("检索范围"), "");
  expect(new URL(requested).searchParams.get("toc_entry_id")).toBe("toc-a");
  await userEvent.click(screen.getByRole("button", { name: "查找" }));
  await vi.waitFor(() =>
    expect(new URL(requested).searchParams.has("toc_entry_id")).toBe(false),
  );
  expect(screen.getByLabelText("current URL").textContent).toContain("q=");
});

test("a directory carrier with several entries exposes each title, without choosing the first", async () => {
  const target = {
    chapter_id: "body",
    run_id: "run",
    physical_page: 8,
    start: 12,
    end: 17,
    text_sha256: "a".repeat(64),
    anchor: "登记120",
    image_sha256: "",
  };
  respondWith((request) =>
    request.url.includes("reading-navigation")
      ? { entries: [], state: "available", version: 1, sha256: "nav" }
      : [
          {
            id: "hit",
            book_id: "book",
            run_id: "run",
            chapter_id: "directory",
            title: "目录",
            text: "多条目录",
            pages: [2],
            start: 0,
            end: 12,
            score: 1,
            doc_kind: "navigation",
            reading_target: null,
            directory_links: [
              {
                id: "a",
                title: "运输",
                level: 1,
                source_start: 0,
                source_end: 4,
                target,
              },
              {
                id: "b",
                title: "税收",
                level: 1,
                source_start: 5,
                source_end: 12,
                target: { ...target, chapter_id: "other" },
              },
            ],
            context: [],
          },
        ],
  );
  render(
    <MemoryRouter initialEntries={["/books/book?q=运输"]}>
      <QueryClientProvider client={new QueryClient()}>
        <SearchPanel bookId="book" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("多条目录");
  expect(screen.queryByRole("link", { name: "阅读对应正文" })).toBeNull();
  expect(
    screen.getByRole("link", { name: "阅读正文：运输" }).getAttribute("href"),
  ).toContain("chapter=body");
  expect(
    screen.getByRole("link", { name: "阅读正文：税收" }).getAttribute("href"),
  ).toContain("chapter=other");
  expect(
    screen.getByRole("link", { name: "查看目录原件第 2 页" }),
  ).toBeTruthy();
});

test("browser back restores query, scope and results after the search panel unmounts", async () => {
  const target = {
    chapter_id: "body",
    run_id: "run",
    physical_page: 8,
    start: 12,
    end: 17,
    text_sha256: "a".repeat(64),
    anchor: "登记120",
    image_sha256: "",
  };
  respondWith((request) =>
    request.url.includes("reading-navigation")
      ? {
          entries: [
            {
              id: "toc-a",
              title: "运输",
              path: ["运输"],
              level: 1,
              reading_target: target,
            },
          ],
          state: "available",
          version: 1,
          sha256: "nav",
        }
      : [
          {
            id: "hit",
            book_id: "book",
            run_id: "run",
            chapter_id: "body",
            title: "运输",
            text: "登记120吨",
            pages: [8],
            start: 12,
            end: 17,
            score: 1,
            reading_target: target,
            context: [],
          },
        ],
  );
  function Flow() {
    const location = useLocation(),
      navigate = useNavigate();
    return location.search.includes("section=text") ? (
      <button onClick={() => navigate(-1)}>返回检索</button>
    ) : (
      <SearchPanel bookId="book" />
    );
  }
  render(
    <MemoryRouter
      initialEntries={[
        "/books/book?tab=reader&section=search&q=粮食&semantic=false&toc_entry_id=toc-a",
      ]}
    >
      <QueryClientProvider client={new QueryClient()}>
        <Flow />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("登记120吨");
  await userEvent.click(screen.getByRole("link", { name: "阅读命中段落" }));
  expect(screen.queryByLabelText("检索本书正文")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "返回检索" }));
  await screen.findByText("登记120吨");
  expect(screen.getByLabelText("检索本书正文")).toHaveProperty("value", "粮食");
  await userEvent.click(screen.getByText("检索选项"));
  expect(screen.getByLabelText("检索范围")).toHaveProperty("value", "toc-a");
  expect(screen.getByLabelText("检索方式")).toHaveProperty("value", "literal");
});

test("a directory with an unresolved ending is visibly unavailable as a scope", async () => {
  const target = {
    chapter_id: "body",
    run_id: "run",
    physical_page: 8,
    start: 12,
    end: 17,
    text_sha256: "a".repeat(64),
    anchor: "登记120",
    image_sha256: "",
  };
  respondWith(() => ({
    entries: [
      {
        id: "toc-a",
        title: "运输",
        path: ["运输"],
        level: 1,
        reading_target: target,
      },
      {
        id: "toc-b",
        title: "未知",
        path: ["未知"],
        level: 1,
        reading_target: null,
      },
    ],
    state: "available",
    version: 1,
    sha256: "nav",
  }));
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <SearchPanel bookId="book" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(screen.getByText("检索选项"));
  expect(
    await screen.findByRole("option", { name: "运输（范围待确认）" }),
  ).toHaveProperty("disabled", true);
});

test("search preserves the hit and exposes separately cited context and its chapter link", async () => {
  let requested = "";
  respondWith((request) => {
    if (request.url.includes("reading-navigation"))
      return { entries: [], state: "available", version: 1, sha256: "nav" };
    requested = request.url;
    return [
      {
        id: "hit",
        book_id: "book",
        run_id: "run",
        chapter_id: "chapter2",
        title: "运输",
        text: "登记120吨",
        pages: [8],
        start: 30,
        end: 36,
        score: 1,
        section_path: ["运输", "甲地"],
        context_truncated: true,
        context: [
          {
            start: 100,
            end: 108,
            text: "不包括乙地",
            pages: [9],
            role: "footnote",
          },
        ],
      },
    ];
  });
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <SearchPanel bookId="book" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.type(screen.getByLabelText("检索本书正文"), "粮食");
  await userEvent.click(screen.getByRole("button", { name: "查找" }));
  await screen.findByText("登记120吨");
  expect(new URL(requested).searchParams.get("semantic")).toBe("true");
  await userEvent.click(screen.getByText("检索选项"));
  await userEvent.selectOptions(screen.getByLabelText("检索方式"), "literal");
  await userEvent.click(screen.getByLabelText("跨章节综合"));
  await userEvent.click(screen.getByRole("button", { name: "查找" }));
  await vi.waitFor(() =>
    expect(new URL(requested).searchParams.get("semantic")).toBe("false"),
  );
  expect(new URL(requested).searchParams.get("diverse")).toBe("true");
  await userEvent.click(screen.getByText("查看相关上下文、表头与注释"));
  const original = new URL(
    screen.getByRole("link", { name: "查看原件第 9 页" }).getAttribute("href")!,
    "http://local",
  );
  expect(original.searchParams.get("chapter")).toBe("chapter2");
  expect(original.searchParams.get("physicalPage")).toBe("9");
  expect(original.searchParams.get("returnTo")).toContain("q=");
  const chapter = new URL(
    screen.getByRole("link", { name: "阅读所在章节" }).getAttribute("href")!,
    "http://local",
  );
  expect(chapter.searchParams.get("chapter")).toBe("chapter2");
  expect(chapter.searchParams.get("returnTo")).toContain("section=search");
  expect(screen.getByText("不包括乙地")).toBeTruthy();
});
