// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReviewPanel from "./ReviewPanel";
import { client } from "@/client/api";
import { RuntimeScopeContext } from "@/app/runtime-scope";
import { respondWith } from "@/test/responses";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function openReview() {
  const issue = {
    id: "ergonomic-page",
    run_id: "ergonomic-run",
    page: 2,
    pages: [2],
    images: [],
    state: "pending",
    revision: 1,
    kind: "page",
    text: "甲错乙，丙误丁。",
    text_sha256: "a".repeat(64),
    reasons: [],
    resolutions: [],
    footnotes: [],
    locations: ["甲错乙", "丙误丁"].map((exact, index) => ({
      id: `doubt-${index}`,
      page: 2,
      anchor: { exact, prefix: "", suffix: "" },
      focus: index ? "误" : "错",
      boxes: [],
      image_precision: "page",
      message: "核对字形",
      source_reading: "",
      candidate: "",
      suggestion: "",
    })),
  };
  respondWith((request) =>
    new URL(request.url).pathname === "/api/v2/reviews" ? [issue] : issue,
  );
  vi.spyOn(client, "PUT").mockImplementation(async (_path, options) => ({
    data: {
      revision: 2,
      draft_text: options!.body.text,
      resolutions: options!.body.resolutions,
    },
    response: new Response(),
  }));
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={query}>
      <RuntimeScopeContext.Provider value="content">
        <ReviewPanel runId="ergonomic-run" bookId="book" />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  return vi.spyOn(client, "POST");
}

test("the active doubt explains the reason without opening machine details", async () => {
  openReview();
  await screen.findByRole("button", { name: "需要修改" });
  const reason = screen.getByText("核对字形");
  expect(reason.closest("details")).toBeNull();
});

test("collapsing the page queue preserves the current correction and review page", async () => {
  openReview();
  await userEvent.click(
    await screen.findByRole("button", { name: "需要修改" }),
  );
  const decisionBar = screen.getByRole("group", { name: "当前决定与提交" });
  expect(
    decisionBar.contains(screen.getByRole("button", { name: /保存修改并/ })),
  ).toBe(true);
  const correction = screen.getByRole("textbox", { name: "本处最终文字" });
  await userEvent.clear(correction);
  await userEvent.type(correction, "正");
  await userEvent.click(screen.getByRole("button", { name: "收起页清单" }));
  expect(screen.queryByRole("navigation", { name: "问题清单" })).toBeNull();
  expect(screen.getByRole("textbox", { name: "本处最终文字" })).toBe(
    correction,
  );
  expect((correction as HTMLTextAreaElement).value).toBe("正");
  await userEvent.click(screen.getByRole("button", { name: "展开页清单" }));
  expect(screen.getByRole("navigation", { name: "问题清单" })).toBeDefined();
  expect((correction as HTMLTextAreaElement).value).toBe("正");
  expect(screen.queryByRole("button", { name: "提交本页并继续" })).toBeNull();
});

test("review shortcuts navigate doubts but never submit from a correction field", async () => {
  const post = openReview();
  (await screen.findByRole("button", { name: "识别正确，保留" })).focus();
  await userEvent.keyboard("{Alt>}{ArrowRight}{/Alt}");
  expect(screen.getByText("疑点 2 / 2")).toBeDefined();
  await userEvent.click(screen.getByRole("button", { name: "需要修改" }));
  screen.getByRole("textbox", { name: "本处最终文字" }).focus();
  await userEvent.keyboard("{Alt>}{ArrowLeft}{Enter}{/Alt}");
  expect(screen.getByText("疑点 2 / 2")).toBeDefined();
  expect(post).not.toHaveBeenCalled();
});
