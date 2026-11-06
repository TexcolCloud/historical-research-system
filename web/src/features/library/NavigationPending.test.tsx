// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import NavigationPanel from "./NavigationPanel";
import { client } from "@/client/api";
import { RuntimeScopeContext } from "@/app/runtime-scope.ts";
import { respondWith } from "@/test/responses";
import { queryKeys } from "@/client/query-keys";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function entry(id: string, status: string, needs_confirmation: boolean) {
  return {
    id,
    title: id,
    level: 1,
    path: [id],
    source_page: 1,
    source_anchor: `${id} 9`,
    source_image: "/toc.png",
    target_label: "9",
    status,
    needs_confirmation,
    reason: "未找到对应正文",
    target: null,
    reading_target: null,
    candidates: [],
  };
}

function report(entries: ReturnType<typeof entry>[]) {
  return {
    state: "available",
    version: 1,
    sha256: "a".repeat(64),
    coverage_complete: true,
    entries,
    approved_pages: [],
    pending_count: entries.filter((e) => e.needs_confirmation).length,
  };
}

function mount(value: ReturnType<typeof report>, reviewOnly = false) {
  respondWith(() => value);
  const query = new QueryClient();
  render(
    <QueryClientProvider client={query}>
      <RuntimeScopeContext.Provider value="content">
        <NavigationPanel runId="run" bookId="book" reviewOnly={reviewOnly} />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  return query;
}

test("unknown workspace scope keeps directory evidence readable and blocks a final no-link decision", async () => {
  respondWith(() => report([entry("待确认目录", "not_found", true)]));
  const post = vi.spyOn(client, "POST");
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NavigationPanel runId="run" bookId="book" reviewOnly />
    </QueryClientProvider>,
  );
  const button = await screen.findByRole("button", {
    name: "保留目录文字，不设置跳转",
  });
  expect(button.hasAttribute("disabled")).toBe(true);
  expect(screen.getByText("待确认目录 9")).toBeDefined();
  await userEvent.click(button);
  expect(post).not.toHaveBeenCalled();
});

test("a complete not-found mapping opens the human task without an explicit entry URL", async () => {
  mount(
    report([
      entry("未找到的条目", "not_found", true),
      entry("还在核验的条目", "waiting_evidence", false),
      entry("已明确不关联", "unlinked_manual", false),
    ]),
    true,
  );
  await screen.findByText(
    "还有 1 项目录需要核对。确认跳转只改变目录导航，正文与原件保留。",
  );
  const editor = screen.getByRole("region", { name: "目录跳转核对" });
  expect(within(editor).getByText("未找到的条目 9")).toBeDefined();
  expect(screen.queryByText("当前没有需要你确认的目录项目。")).toBeNull();
  expect(
    within(editor).getByRole("button", { name: "保留目录文字，不设置跳转" }),
  ).toBeDefined();
});

test("human confirmation and automatic waiting filters use the same server classification", async () => {
  mount(
    report([
      entry("未找到的条目", "not_found", true),
      entry("旧记录待核对", "unresolved", true),
      entry("还在核验的条目", "waiting_evidence", false),
      entry("已明确不关联", "unlinked_manual", false),
    ]),
  );
  await screen.findByRole("button", { name: "需你核对 · 2" });
  await userEvent.click(screen.getByRole("button", { name: "等待核验 · 1" }));
  expect(screen.getByText("还在核验的条目")).toBeDefined();
  expect(screen.queryByText("未找到的条目")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "需你核对 · 2" }));
  expect(screen.getByText("未找到的条目")).toBeDefined();
  expect(screen.getByText("旧记录待核对")).toBeDefined();
  expect(screen.queryByText("还在核验的条目")).toBeNull();
});

test("an explicit final no-link decision updates the book count and advances to the remaining human task", async () => {
  let current = report([
    entry("未找到的条目", "not_found", true),
    entry("旧记录待核对", "unresolved", true),
    entry("还在核验的条目", "waiting_evidence", false),
  ]);
  respondWith(() => current);
  const post = vi.spyOn(client, "POST").mockImplementation(async () => {
    const next = current.entries.find((e) => e.needs_confirmation)!;
    current = report(
      current.entries.map((e) =>
        e.id === next.id
          ? { ...e, status: "unlinked_manual", needs_confirmation: false }
          : e,
      ),
    );
    return { data: current, response: new Response() };
  });
  const query = new QueryClient();
  query.setQueryData(["platform", "book", "book"], {
    id: "book",
    navigation_pending_count: 2,
  });
  const published = { sha256: "old-published", entries: [] };
  query.setQueryData(queryKeys.readingNavigation("book"), published);
  render(
    <QueryClientProvider client={query}>
      <RuntimeScopeContext.Provider value="content">
        <NavigationPanel runId="run" bookId="book" reviewOnly />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "保留目录文字，不设置跳转" }),
  );
  await screen.findByText(
    "还有 1 项目录需要核对。确认跳转只改变目录导航，正文与原件保留。",
  );
  expect(
    screen.getByRole("region", { name: "目录跳转核对" }).textContent,
  ).toContain("旧记录待核对 9");
  expect(query.getQueryData(["platform", "book", "book"])).toMatchObject({
    navigation_pending_count: 1,
  });
  expect(
    query.getQueryState(queryKeys.readingNavigation("book"))?.isInvalidated,
  ).toBe(true);
  expect(query.getQueryData(queryKeys.readingNavigation("book"))).toEqual(
    published,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "保留目录文字，不设置跳转" }),
  );
  await screen.findByRole("heading", { name: "当前目录核对完成" });
  expect(query.getQueryData(["platform", "book", "book"])).toMatchObject({
    navigation_pending_count: 0,
  });
  expect(post).toHaveBeenCalledTimes(2);
  expect(post).toHaveBeenLastCalledWith(
    "/api/v2/runs/{run_id}/navigation/{entry_id}/decision",
    expect.objectContaining({
      body: expect.objectContaining({ target_page: null, target_anchor: "" }),
    }),
  );
});

test("a missing explicit entry falls back to the real remaining human task instead of an empty queue", async () => {
  respondWith(() => report([entry("实际待核对", "not_found", true)]));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RuntimeScopeContext.Provider value="content">
        <NavigationPanel
          runId="run"
          bookId="book"
          reviewOnly
          entryId="old-entry"
        />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  const editor = await screen.findByRole("region", { name: "目录跳转核对" });
  expect(within(editor).getByText("实际待核对 9")).toBeDefined();
  expect(screen.queryByText("当前没有需要核对的目录")).toBeNull();
});

test("an external final decision closes an automatically selected task when the queue becomes empty", async () => {
  const initial = report([
    entry("第一条", "not_found", true),
    entry("下一条", "not_found", true),
  ]);
  const afterFirst = report([
    entry("第一条", "unlinked_manual", false),
    entry("下一条", "not_found", true),
  ]);
  vi.spyOn(client, "POST").mockResolvedValue({
    data: afterFirst,
    response: new Response(),
  });
  const query = mount(initial, true);
  await userEvent.click(
    await screen.findByRole("button", { name: "保留目录文字，不设置跳转" }),
  );
  await screen.findByText("下一条 9");
  await act(async () => {
    query.setQueryData(
      queryKeys.navigation("run"),
      report([
        entry("第一条", "unlinked_manual", false),
        entry("下一条", "unlinked_manual", false),
      ]),
    );
  });
  await screen.findByRole("heading", { name: "当前目录核对完成" });
  expect(screen.queryByRole("region", { name: "目录跳转核对" })).toBeNull();
});

test("an explicit reader entry can still reopen a completed directory mapping", async () => {
  respondWith(() => report([entry("重新核对的条目", "unlinked_manual", false)]));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RuntimeScopeContext.Provider value="content">
        <NavigationPanel runId="run" bookId="book" reviewOnly entryId="重新核对的条目" />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  const editor = await screen.findByRole("region", { name: "目录跳转核对" });
  expect(within(editor).getByText("重新核对的条目 9")).toBeDefined();
});
