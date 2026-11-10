// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import NavigationPanel from "./NavigationPanel";
import { client } from "@/client/api";
import { respondWith } from "@/test/responses";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("published directory recovers a read failure without requiring or submitting human scope", async () => {
  const read = vi
    .spyOn(client, "GET")
    .mockRejectedValueOnce(new Error("temporary read failure"));
  const post = vi.spyOn(client, "POST");
  respondWith(() => ({
    state: "available",
    version: 1,
    sha256: "a".repeat(64),
    coverage_complete: true,
    pending_count: 1,
    approved_pages: [],
    entries: [
      {
        id: "entry",
        title: "待定位的目录",
        level: 1,
        path: ["待定位的目录"],
        source_page: 1,
        source_anchor: "待定位的目录 9",
        source_image: "/toc.png",
        target_label: "9",
        status: "not_found",
        needs_confirmation: true,
        reason: "节选没有对应正文",
        target: null,
        reading_target: null,
      },
    ],
  }));
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <NavigationPanel runId="native-run" bookId="book" published />
    </QueryClientProvider>,
  );
  expect((await screen.findByRole("alert")).textContent).toContain(
    "temporary read failure",
  );
  await userEvent.click(screen.getByRole("button", { name: "重新读取目录" }));
  await screen.findByText("待定位的目录");
  await userEvent.click(screen.getByRole("button", { name: "需你核对 · 1" }));
  const list = screen.getByRole("list", { name: "原书目录" });
  expect(
    within(list)
      .getByRole("link", { name: "核对这条目录" })
      .getAttribute("href"),
  ).toContain("entry=entry");
  expect(read).toHaveBeenCalledTimes(2);
  expect(read).toHaveBeenCalledWith(
    "/api/v2/books/{book_id}/reading-navigation",
    expect.any(Object),
  );
  expect(screen.queryByRole("button", { name: "更新推荐" })).toBeNull();
  expect(screen.queryByRole("region", { name: "目录跳转核对" })).toBeNull();
  expect(post).not.toHaveBeenCalled();
});
