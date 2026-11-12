// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import AppShell from "./AppShell";
import { respondWith } from "@/test/responses";
import { research } from "@/features/research/api";
import { DashboardPage } from "@/features/studies/StudyExtras";

vi.mock("@/hooks/useWorkspaceEvents.ts", () => ({
  useWorkspaceEvents: () => true,
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

function shell(entry: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ validation_scope: "content" })),
  );
  respondWith((request) =>
    request.url.includes("/runtime") ? { validation_scope: "content" } : [],
  );
  const cache = new QueryClient();
  cache.setQueryData(["platform", "runtime"], { validation_scope: "content" });
  cache.setQueryData(["studies", "preferences"], { compact: false });
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <QueryClientProvider client={cache}>
        <Routes>
          <Route element={<AppShell />}>
            <Route
              path="*"
              element={
                <>
                  <p>当前工作</p>
                  <Link to="/books/book?tab=reader">打开阅读模式</Link>
                  <Link to="/books/book?tab=reader&physicalPage=3">
                    跳到下一处
                  </Link>
                </>
              }
            />
          </Route>
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

test("global navigation separates five work destinations from settings and topic views", () => {
  shell("/dashboard");
  const nav = within(screen.getByRole("navigation", { name: "主要导航" }));
  expect(
    nav
      .getAllByRole("link")
      .map((link) => link.textContent?.replace(/\s+/g, " ").trim()),
  ).toEqual([
    "继续工作",
    "史料集",
    "全文检索",
    "研究专题",
    "待办 资料待读取研究待读取",
  ]);
  expect(nav.queryByRole("link", { name: "AI 分析" })).toBeNull();
  expect(screen.getByRole("navigation", { name: "工作空间设置" })).toBeTruthy();
});

test("entering a reading mode frees the sidebar but moving within that mode respects a manual expansion", async () => {
  shell("/books/book?tab=review");
  const user = userEvent.setup();
  expect(
    screen.queryByRole("complementary", { name: "研究功能导航" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "展开功能导航" }));
  expect(
    screen.getByRole("complementary", { name: "研究功能导航" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("link", { name: "打开阅读模式" }));
  expect(
    screen.queryByRole("complementary", { name: "研究功能导航" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "展开功能导航" }));
  await user.click(screen.getByRole("link", { name: "跳到下一处" }));
  expect(
    screen.getByRole("complementary", { name: "研究功能导航" }),
  ).toBeTruthy();
});

test("home continues the last verified workspace book location without changing its source target", async () => {
  const book = {
    id: "book",
    title: "原件甲",
    state: "ready",
    pending_count: 0,
  };
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  cache.setQueryData(["platform", "runtime"], {
    workspace_id: "workspace-a",
    validation_scope: "content",
  });
  cache.setQueryData(["platform", "books"], [book]);
  cache.setQueryData(["studies", "preferences"], { compact: false });
  respondWith((request) =>
    request.url.includes("/runtime")
      ? { workspace_id: "workspace-a", validation_scope: "content" }
      : [book],
  );
  vi.spyOn(research, "pending").mockResolvedValue({
    items: [],
    total: 0,
    offset: 0,
    limit: 50,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ items: [], total: 0, offset: 0, limit: 100 }),
    ),
  );
  const retained =
    "/books/book?tab=reader&physicalPage=9&start=12&end=20&textSha=retained-source";
  render(
    <MemoryRouter initialEntries={[retained]}>
      <QueryClientProvider client={cache}>
        <Routes>
          <Route element={<AppShell />}>
            <Route
              path="/books/:bookId"
              element={<Link to="/dashboard">返回继续工作</Link>}
            />
            <Route path="/dashboard" element={<DashboardPage />} />
          </Route>
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(screen.getByRole("link", { name: "返回继续工作" }));
  expect(
    (await screen.findByRole("link", { name: "继续上次工作" })).getAttribute(
      "href",
    ),
  ).toBe(retained);
  expect(
    screen.getByRole("region", { name: "上次工作现场" }).textContent,
  ).toContain("阅读：原件甲");
});

test("the sidebar separates pending books from research records and does not fetch research only for a badge", async () => {
  const book = {
    id: "pending",
    title: "原件",
    state: "awaiting_review",
    pending_count: 5,
  };
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  cache.setQueryData(["platform", "runtime"], {
    workspace_id: "workspace-a",
    validation_scope: "content",
  });
  cache.setQueryData(["platform", "books"], [book]);
  cache.setQueryData(["studies", "preferences"], { compact: false });
  respondWith((request) =>
    request.url.includes("/runtime")
      ? { workspace_id: "workspace-a", validation_scope: "content" }
      : [book],
  );
  const pending = vi
    .spyOn(research, "pending")
    .mockResolvedValue({ items: [], total: 2, offset: 0, limit: 50 });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ compact: false })),
  );
  render(
    <MemoryRouter initialEntries={["/library"]}>
      <QueryClientProvider client={cache}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/library" element={<p>资料列表</p>} />
          </Route>
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const nav = screen.getByRole("link", { name: "待办" });
  expect(nav.textContent).toContain("1本资料");
  expect(nav.textContent).toContain("研究待读取");
  expect(pending).not.toHaveBeenCalled();
});

test("a cached research count keeps its own unit and truncation instead of being added to the book count", () => {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const book = {
    id: "pending",
    title: "原件",
    state: "awaiting_review",
    pending_count: 5,
  };
  cache.setQueryData(["platform", "runtime"], {
    workspace_id: "workspace-a",
    validation_scope: "content",
  });
  cache.setQueryData(["platform", "books"], [book]);
  cache.setQueryData(["studies", "preferences"], { compact: false });
  cache.setQueryData(["research", "pending", "", 0], {
    items: [],
    total: 7,
    offset: 0,
    limit: 50,
    truncated: true,
  });
  respondWith((request) =>
    request.url.includes("/runtime")
      ? { workspace_id: "workspace-a", validation_scope: "content" }
      : [book],
  );
  const pending = vi
    .spyOn(research, "pending")
    .mockResolvedValue({ items: [], total: 7, offset: 0, limit: 50 });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ compact: false })),
  );
  render(
    <MemoryRouter initialEntries={["/library"]}>
      <QueryClientProvider client={cache}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/library" element={<p>资料列表</p>} />
          </Route>
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const nav = screen.getByRole("link", { name: "待办" });
  expect(nav.textContent).toContain("1本资料");
  expect(nav.textContent).toContain("至少7项研究");
  expect(nav.getAttribute("title")).toBe("1本资料 · 至少7项研究");
  expect(pending).not.toHaveBeenCalled();
});
