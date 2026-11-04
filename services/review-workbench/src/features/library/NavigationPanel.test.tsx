// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import NavigationPanel from "./NavigationPanel.tsx";
import { client } from "@/client/api.ts";
import { respondWith } from "@/test/responses.ts";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("a conflicting printed page stays unlinked until a source-bound navigation decision", async () => {
  const target = {
    physical_page: 3,
    printed_label: "39",
    anchor: "1936年",
    start: 2,
    markdown_sha256: "b".repeat(64),
    image_sha256: "c".repeat(64),
  };
  const report = {
    state: "available",
    version: 2,
    sha256: "a".repeat(64),
    coverage_complete: true,
    approved_pages: [{ page: 3, headings: ["1936年"], printed_label: "39" }],
    entries: [
      {
        id: "entry",
        title: "1936年",
        level: 1,
        path: ["1936年"],
        source_page: 1,
        source_anchor: "1936年 38",
        source_image: "/original-toc.png",
        target_label: "38",
        status: "conflict",
        reason: "目录与正文页码不一致",
        target: null,
        candidates: [target],
      },
    ],
  };
  respondWith(() => report);
  const post = vi.spyOn(client, "POST").mockResolvedValue({
    data: {
      ...report,
      entries: [{ ...report.entries[0]!, status: "matched_manual", target }],
    },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NavigationPanel runId="run" bookId="book" />
    </QueryClientProvider>,
  );
  await screen.findByText(/需要确认位置/);
  expect(screen.queryByText("阅读正文 →")).toBeNull();
  expect(
    screen.getByRole("link", { name: /目录原图/ }).getAttribute("href"),
  ).toBe("/original-toc.png");
  await userEvent.click(screen.getByRole("button", { name: "核对目录位置" }));
  await userEvent.click(
    screen.getByRole("button", { name: /选择候选.*原件3页/ }),
  );
  await userEvent.click(screen.getByRole("button", { name: "确认此位置" }));
  expect(post).toHaveBeenCalledWith(
    "/api/v2/runs/{run_id}/navigation/{entry_id}/decision",
    expect.objectContaining({
      body: {
        expected_sha256: report.sha256,
        target_page: 3,
        target_anchor: "1936年",
      },
    }),
  );
});
