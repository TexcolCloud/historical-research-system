// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { GlobalSearchPage, SettingsPage } from "./StudyExtras";
import { respondWith } from "@/test/responses";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("a preference conflict can reload the current revision before another PATCH", async () => {
  respondWith(() => ({
    workspace_id: "test-workspace",
    validation_scope: "content",
  }));
  let version = 0;
  const patches: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        patches.push(JSON.parse(String(init.body)));
        if (patches.length === 1) {
          version = 2;
          return Response.json(
            { detail: "偏好已在另一窗口更新。" },
            { status: 409 },
          );
        }
        return Response.json({ ...patches[1], version: 3 });
      }
      return Response.json({
        version,
        compact: version > 0,
        restore_last_canvas: true,
      });
    }),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <SettingsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await waitFor(() =>
    expect(
      (
        screen.getByRole("checkbox", {
          name: "紧凑显示研究桌面",
        }) as HTMLInputElement
      ).disabled,
    ).toBe(false),
  );
  await user.click(screen.getByRole("checkbox", { name: "紧凑显示研究桌面" }));
  expect(await screen.findByText("偏好已在另一窗口更新。")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "重新读取偏好" }));
  await waitFor(() =>
    expect(
      (
        screen.getByRole("checkbox", {
          name: "紧凑显示研究桌面",
        }) as HTMLInputElement
      ).checked,
    ).toBe(true),
  );
  await user.click(
    screen.getByRole("checkbox", { name: "进入专题时恢复上次画布" }),
  );
  await waitFor(() => expect(patches).toHaveLength(2));
  expect(patches).toEqual([
    { expected_version: 0, compact: true, restore_last_canvas: true },
    { expected_version: 2, compact: true, restore_last_canvas: false },
  ]);
});

test("global retrieval preserves a separate selection target when the query changes", async () => {
  const requests: { url: string; method?: string; body?: string }[] = [];
  const topics = ["search-topic", "target-topic"].map((id) => ({
    id,
    title: id === "search-topic" ? "检索专题" : "选材专题",
    description: "",
    version: 2,
    archived: false,
    materials: [],
    book_ids: [],
  }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({
        url,
        method: init?.method,
        body: init?.body as string | undefined,
      });
      if (url.includes("/search?"))
        return Response.json({
          query: "交通",
          available: true,
          backend: "hybrid",
          evidence_status: "current",
          hits: [
            {
              id: "hit",
              book_id: "book",
              book_title: "史料集",
              title: "交通来函",
              pages: [3],
              text: "来函原文",
              research_cards: [{ id: "card", title: "交通来函", version: 1 }],
            },
          ],
        });
      if (url.endsWith("/materials")) return Response.json(topics[1]);
      const topic = topics.find((value) => url.endsWith(`/topics/${value.id}`));
      return Response.json(
        topic ?? { items: topics, total: 2, offset: 0, limit: 100 },
      );
    }),
  );
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MemoryRouter
      initialEntries={["/search?q=交通&topic=search-topic&addTo=target-topic"]}
    >
      <QueryClientProvider client={query}>
        <GlobalSearchPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("来函原文");
  const user = userEvent.setup();
  await user.clear(screen.getByRole("textbox", { name: "全文检索关键词" }));
  await user.type(
    screen.getByRole("textbox", { name: "全文检索关键词" }),
    "铁路",
  );
  await user.click(screen.getByRole("button", { name: "检索原文" }));
  await waitFor(() =>
    expect(
      requests.some((request) => request.url.includes("q=%E9%93%81%E8%B7%AF")),
    ).toBe(true),
  );
  await user.click(await screen.findByRole("button", { name: "加入专题" }));
  await waitFor(() =>
    expect(
      requests.find((request) => request.method === "POST")?.url,
    ).toContain("/topics/target-topic/materials"),
  );
  const searches = requests.filter((request) =>
    request.url.includes("/search?"),
  );
  expect(
    searches.every(
      (request) =>
        new URL(request.url, "http://localhost").searchParams.get(
          "topic_id",
        ) === "search-topic",
    ),
  ).toBe(true);
});
