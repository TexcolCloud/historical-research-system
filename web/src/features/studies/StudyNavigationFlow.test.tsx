// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { TopicsPage } from "./StudyTopics";
import { respondWith } from "@/test/responses";
import { CanvasLandingPage, DashboardPage } from "./StudyExtras";
import { research } from "@/features/research/api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("home prioritizes unresolved work and running books before recent topics", async () => {
  respondWith((request) =>
    request.url.includes("/books")
      ? [
          {
            id: "review",
            title: "待核原件",
            state: "awaiting_review",
            pending_count: 3,
          },
          {
            id: "running",
            title: "正在处理的原件",
            state: "running",
            pending_count: 0,
          },
        ]
      : { workspace_id: "workspace-a", validation_scope: "content" },
  );
  vi.spyOn(research, "pending").mockResolvedValue({
    items: [],
    total: 2,
    offset: 0,
    limit: 50,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ items: [], total: 0, offset: 0, limit: 100 }),
    ),
  );
  render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <DashboardPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByRole("heading", { name: "继续工作" })).toBeTruthy();
  const pending = await screen.findByRole("region", { name: "待你核对" });
  expect(pending.textContent).toContain("待核原件");
  expect(
    screen.getByRole("region", { name: "后台处理中" }).textContent,
  ).toContain("正在处理的原件");
  expect(
    screen.getByRole("link", { name: "开始核对" }).getAttribute("href"),
  ).toBe("/reviews");
});

test("the legacy workspace link opens its explicit topic directly", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ items: [], total: 0, offset: 0, limit: 100 }),
    ),
  );
  render(
    <MemoryRouter initialEntries={["/workspace?topic=topic"]}>
      <QueryClientProvider client={new QueryClient()}>
        <Routes>
          <Route path="/workspace" element={<CanvasLandingPage />} />
          <Route
            path="/studies/topic/canvas"
            element={<h1>已打开所选专题画布</h1>}
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(
    await screen.findByRole("heading", { name: "已打开所选专题画布" }),
  ).toBeTruthy();
});

test("a legacy workspace without a topic returns to the existing topic selector instead of choosing one", async () => {
  render(
    <MemoryRouter initialEntries={["/workspace"]}>
      <QueryClientProvider client={new QueryClient()}>
        <Routes>
          <Route path="/workspace" element={<CanvasLandingPage />} />
          <Route path="/studies" element={<h1>选择研究专题</h1>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(
    await screen.findByRole("heading", { name: "选择研究专题" }),
  ).toBeTruthy();
});

test("an opened topic uses one research space instead of repeating the topic selection list", async () => {
  const topic = {
    id: "topic",
    title: "交通研究",
    description: "",
    materials: [],
    book_ids: [],
    version: 1,
    archived: false,
  };
  respondWith(() => ({
    workspace_id: "workspace-a",
    validation_scope: "content",
  }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      return Response.json(
        url.endsWith("/topics/topic")
          ? topic
          : { items: [topic], total: 1, offset: 0, limit: 100 },
      );
    }),
  );
  render(
    <MemoryRouter initialEntries={["/studies?topic=topic"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <TopicsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: "交通研究" });
  expect(screen.queryByRole("complementary", { name: "专题清单" })).toBeNull();
  expect(screen.getByRole("navigation", { name: "专题内导航" })).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "研究记录" }).getAttribute("href"),
  ).toBe("/arguments?topic=topic");
});
