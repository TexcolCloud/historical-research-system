// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import IssueEditor from "./IssueEditor.tsx";
import SourceFocus from "./SourceFocus.tsx";
import type { components } from "@/client/schema.d.ts";

afterEach(() => cleanup());

function location(): components["schemas"]["ReviewLocation"] {
  return {
    id: "visible",
    page: 1,
    anchor: { exact: "甲错乙", prefix: "", suffix: "" },
    focus: "错",
    boxes: [[0.2, 0.3, 0.4, 0.35]],
    image_precision: "line",
    message: "核对这个字",
    candidate: "甲错乙",
    source_reading: "甲对乙",
    suggestion: "",
    text_sha256: "a".repeat(64),
    image_sha256: "b".repeat(64),
    text_status: "located",
    text_precision: "character",
    offset_unit: "unicode_codepoint",
    location: "",
    unlocated_reason: "",
  };
}

test("the current text highlight stays visible beside the original without a display switch", () => {
  const { container } = render(
    <QueryClientProvider client={new QueryClient()}>
      <IssueEditor
        issue={{
          id: "page",
          run_id: "run",
          page: 1,
          revision: 1,
          draft_conflict: false,
          state: "pending",
          text_sha256: "a".repeat(64),
          text: "甲错乙",
          reasons: [],
          kind: "page",
          pages: [1],
          images: ["/original.png"],
          footnotes: [],
          locations: [location()],
          resolutions: [],
        }}
        workflowTest={false}
        onSaved={vi.fn()}
      />
    </QueryClientProvider>,
  );
  const mark = container.querySelector("mark[data-review-id=visible]");
  expect(mark?.textContent).toBe("错");
  expect(
    screen.getByRole("region", { name: "整页文字与上下文" }).contains(mark),
  ).toBe(true);
  expect(screen.getByRole("complementary", { name: "原书对照" })).toBeDefined();
  expect(screen.queryByRole("navigation", { name: "核对显示方式" })).toBeNull();
  expect(mark?.closest("details")).toBeNull();
});

test("a source-only doubt shows a literal Markdown excerpt when it cannot be marked in rendered text", async () => {
  const loc = {
    ...location(),
    anchor: { exact: "错字", prefix: "", suffix: "" },
    focus: "错",
    candidate: "错字",
  };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <IssueEditor
        issue={{
          id: "source-only",
          run_id: "run",
          page: 1,
          revision: 1,
          draft_conflict: false,
          state: "pending",
          text_sha256: "a".repeat(64),
          text: "[链接](https://example.test/错字)",
          reasons: [],
          kind: "page",
          pages: [1],
          images: [],
          footnotes: [],
          locations: [loc],
          resolutions: [],
        }}
        workflowTest={false}
        onSaved={vi.fn()}
      />
    </QueryClientProvider>,
  );
  const excerpt = await screen.findByRole("region", {
    name: "当前疑点的原文定位",
  });
  expect(excerpt.querySelector("mark")?.textContent).toBe("错字");
  expect(
    screen.getByRole("region", { name: "整页文字与上下文" }).contains(excerpt),
  ).toBe(true);
});

test("a requested crop includes surrounding original context and preserves the evidence frame", async () => {
  const loc = location();
  render(<SourceFocus src="/original.png" issue={loc} onBox={vi.fn()} />);
  expect(screen.queryByLabelText("疑点局部原图")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "当前区域" }));
  const crop = screen.getByLabelText("疑点局部原图");
  const rect = crop.querySelector("rect");
  expect(rect?.getAttribute("x")).toBe("0.2");
  expect(rect?.getAttribute("y")).toBe("0.3");
  const view = crop.getAttribute("viewBox")!.split(" ").map(Number);
  expect(view[0]).toBeLessThan(0.2);
  expect(view[1]).toBeLessThan(0.3);
  expect(crop.closest("details")).toBeNull();
  expect(
    screen.getByRole("slider", { name: "原图放大倍数" }).getAttribute("value"),
  ).toBe("100");
});

test("one doubt can navigate every source box and a full-page box selects its crop", async () => {
  const loc = {
    ...location(),
    boxes: [
      [0.1, 0.2, 0.3, 0.25],
      [0.4, 0.5, 0.6, 0.55],
      [0.7, 0.8, 0.9, 0.85],
    ],
  };
  render(<SourceFocus src="/original.png" issue={loc} onBox={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: "当前区域" }));
  expect(screen.getByText("位置 1 / 3")).toBeDefined();
  await userEvent.click(screen.getByRole("button", { name: "下一处原图位置" }));
  expect(screen.getByText("位置 2 / 3")).toBeDefined();
  expect(
    screen
      .getByLabelText("疑点局部原图")
      .querySelector("rect")
      ?.getAttribute("x"),
  ).toBe("0.4");
  await userEvent.click(screen.getByRole("button", { name: "整页" }));
  await userEvent.click(
    screen.getByRole("button", { name: "原图疑点 1，位置 3" }),
  );
  expect(screen.getByText("位置 3 / 3")).toBeDefined();
  await userEvent.click(screen.getByRole("button", { name: "当前区域" }));
  expect(
    screen
      .getByLabelText("疑点局部原图")
      .querySelector("rect")
      ?.getAttribute("x"),
  ).toBe("0.7");
});

test("choosing a doubt locates its text without scrolling again on a background revision", async () => {
  const scroll = vi.fn();
  const previous = HTMLElement.prototype.scrollTo;
  HTMLElement.prototype.scrollTo = scroll;
  const query = new QueryClient();
  const issue: components["schemas"]["ReviewIssue"] = {
    id: "selection",
    run_id: "run",
    page: 1,
    revision: 1,
    draft_conflict: false,
    state: "pending",
    text_sha256: "a".repeat(64),
    text: "甲错乙。丙误丁。",
    reasons: [],
    kind: "page",
    pages: [1],
    images: [],
    footnotes: [],
    resolutions: [],
    locations: [
      location(),
      {
        ...location(),
        id: "second",
        anchor: { exact: "丙误丁", prefix: "甲错乙。", suffix: "。" },
        focus: "误",
        candidate: "丙误丁",
      },
    ],
  };
  const editor = (revision: number) => (
    <QueryClientProvider client={query}>
      <IssueEditor
        issue={{ ...issue, revision }}
        workflowTest={false}
        onSaved={vi.fn()}
      />
    </QueryClientProvider>
  );
  try {
    const view = render(editor(1));
    await waitFor(() => expect(scroll).toHaveBeenCalledOnce());
    expect(scroll.mock.contexts[0]).toBe(
      document.querySelector(".review-page-preview"),
    );
    scroll.mockClear();
    await userEvent.click(screen.getByRole("button", { name: "2 · 待核" }));
    await waitFor(() => expect(scroll).toHaveBeenCalledOnce());
    expect(scroll.mock.contexts[0]).toBe(
      document.querySelector(".review-page-preview"),
    );
    scroll.mockClear();
    view.rerender(editor(2));
    expect(scroll).not.toHaveBeenCalled();
  } finally {
    HTMLElement.prototype.scrollTo = previous;
    query.clear();
  }
});
