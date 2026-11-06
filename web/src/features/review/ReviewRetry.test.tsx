// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReviewPanel from "./ReviewPanel.tsx";
import { RuntimeScopeContext } from "@/app/runtime-scope.ts";
import { client } from "@/client/api.ts";
import { queryKeys } from "@/client/query-keys.ts";
import { respondWith } from "@/test/responses.ts";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test.each([false, true])(
  "a repeated review refreshes doubts and retains local edits (same text: %s)",
  async (sameText) => {
    const location = {
      id: "old",
      page: 1,
      anchor: { exact: "甲错乙", prefix: "", suffix: "" },
      focus: "错",
      message: "旧疑点",
      boxes: [],
      image_precision: "page",
      candidate: "错",
      source_reading: "",
      suggestion: "",
    };
    const original = {
      id: "reopened",
      run_id: "run",
      page: 1,
      pages: [1],
      images: [],
      state: "pending",
      revision: 1,
      text: "甲错乙",
      text_sha256: "a".repeat(64),
      review_input_sha256: "1".repeat(64),
      kind: "page",
      reasons: [],
      resolutions: [],
      locations: [location],
    };
    const latest = {
      ...original,
      revision: 3,
      text: sameText ? original.text : "甲新乙",
      text_sha256: (sameText ? "a" : "b").repeat(64),
      review_input_sha256: "2".repeat(64),
      locations: [
        {
          ...location,
          id: "new",
          message: "新疑点",
          anchor: {
            ...location.anchor,
            exact: sameText ? original.text : "甲新乙",
          },
          focus: sameText ? "错" : "新",
        },
      ],
    };
    let current = original;
    respondWith((request) =>
      new URL(request.url).pathname === "/api/v2/reviews" ? [current] : current,
    );
    let oldSave!: () => void;
    const late = new Promise<void>((resolve) => {
      oldSave = resolve;
    });
    vi.spyOn(client, "PUT").mockImplementation(async () => {
      await late;
      return {
        data: { revision: 2, draft_text: "甲改乙", resolutions: [] },
        response: new Response(),
      };
    });
    const query = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { container } = render(
      <QueryClientProvider client={query}>
        <RuntimeScopeContext.Provider value="content">
          <ReviewPanel runId="run" />
        </RuntimeScopeContext.Provider>
      </QueryClientProvider>,
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "需要修改" }),
    );
    const input = screen.getByRole("textbox", { name: "本处最终文字" });
    await userEvent.clear(input);
    await userEvent.type(input, "改");
    current = latest;
    await query.invalidateQueries({ queryKey: queryKeys.issue(original.id) });
    await screen.findByText("新疑点");
    expect(screen.getByRole("note").textContent).toContain("原草稿已保留");
    expect(
      container.querySelector(".review-page-preview")?.textContent,
    ).toContain("甲改乙");
    expect(screen.getByText("已处理 0 / 1 处")).toBeDefined();
    oldSave();
    await waitFor(() =>
      expect(
        query.getQueryData<{ review_input_sha256: string }>(
          queryKeys.issue(original.id),
        )?.review_input_sha256,
      ).toBe(latest.review_input_sha256),
    );
    expect(screen.getByText("新疑点")).toBeDefined();
    query.clear();
  },
);
