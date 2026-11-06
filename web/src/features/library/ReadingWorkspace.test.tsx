// @vitest-environment happy-dom
import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ReadingWorkspace from "./ReadingWorkspace.tsx";
import { respondWith } from "@/test/responses.ts";
afterEach(cleanup);
test("unpublished search explains availability and takes the reader directly to the retained draft", async () => {
  const calls: string[] = [];
  respondWith((request) => {
    calls.push(request.url);
    return request.url.includes("/structure")
      ? { items: [] }
      : {
          page: 1,
          page_count: 1,
          machine_status: "unreviewed-source",
          text: "保留的识别底稿",
          image: "/original.png",
          footnotes: [],
        };
  });
  render(
    <MemoryRouter initialEntries={["/?tab=search"]}>
      <QueryClientProvider client={new QueryClient()}>
        <ReadingWorkspace
          book={{
            id: "book",
            title: "材料",
            state: "running",
            run_id: "run",
            stage: "vision",
            created_at: "2026-11-05",
            revision: 1,
            revision_pending: false,
            pending_count: 1,
            navigation_pending_count: 0,
          }}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("heading", { name: "本书尚未建立检索索引" }),
  ).toBeDefined();
  expect(screen.queryByRole("button", { name: "查找" })).toBeNull();
  expect(calls.some((url) => url.includes("/search"))).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "先阅读底稿" }));
  await screen.findByText("保留的识别底稿");
  expect(
    screen.getByRole("link", { name: "去核对 1 页正文" }).getAttribute("href"),
  ).toBe("#/books/book?tab=review");
});
