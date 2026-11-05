// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { components } from "@/client/schema.d.ts";
import { client } from "@/client/api.ts";
import { decisionRange } from "@/editor/review-highlights.ts";
import { useReviewSession } from "./useReviewSession.ts";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function wrapper() {
  const query = new QueryClient();
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={query}>{children}</QueryClientProvider>
  );
}

function issueFixture(): components["schemas"]["ReviewIssue"] {
  return {
    id: "draft-positions",
    run_id: "run",
    page: 1,
    pages: [1],
    images: [],
    state: "pending",
    revision: 1,
    text_sha256: "a".repeat(64),
    kind: "page",
    text: "😀甲错乙，丙错丁。",
    reasons: [],
    resolutions: [],
    locations: [
      {
        id: "a",
        page: 1,
        anchor: { exact: "甲错乙", prefix: "😀", suffix: "，丙错丁。" },
        focus: "错",
        boxes: [],
        image_precision: "page",
        message: "核对",
        candidate: "甲错乙",
        source_reading: "",
        suggestion: "",
        text_sha256: "a".repeat(64),
        image_sha256: "b".repeat(64),
        text_status: "located",
        text_precision: "character",
        offset_unit: "unicode_codepoint",
        location: "",
        unlocated_reason: "",
      },
      {
        id: "b",
        page: 1,
        anchor: { exact: "丙错丁", prefix: "😀甲错乙，", suffix: "。" },
        focus: "错",
        boxes: [],
        image_precision: "page",
        message: "核对",
        candidate: "丙错丁",
        source_reading: "",
        suggestion: "",
        text_sha256: "a".repeat(64),
        image_sha256: "b".repeat(64),
        text_status: "located",
        text_precision: "character",
        offset_unit: "unicode_codepoint",
        location: "",
        unlocated_reason: "",
      },
    ],
  };
}

test("drafts save both edited positions and restore them after reopening without local cache", async () => {
  const issue = issueFixture();
  const put = vi
    .spyOn(client, "PUT")
    .mockImplementation(async (_p, options) => ({
      data: {
        revision: 2,
        draft_text: options!.body.text,
        resolutions: options!.body.resolutions,
        locations: options!.body.locations,
        locations_text_sha256: "b".repeat(64),
      },
      response: new Response(),
    }));
  const session = renderHook(() => useReviewSession(issue, vi.fn()), {
    wrapper: wrapper(),
  });
  for (const id of ["a", "b"]) {
    act(() => session.result.current.setActive(id));
    act(() => session.result.current.beginEdit());
    act(() => session.result.current.changeFocus("对"));
    await act(() => session.result.current.finish("edit"));
  }
  const body = put.mock.calls.at(-1)?.[1]?.body;
  expect(body?.text).toBe("😀甲对乙，丙对丁。");
  expect(body?.locations).toHaveLength(2);
  expect(body?.locations?.map((row) => row.focus)).toEqual(["对", "对"]);
  const saved = {
    ...issue,
    draft_text: body!.text,
    draft_locations: body!.locations,
    draft_locations_text_sha256: "b".repeat(64),
    resolutions: [],
  };
  session.unmount();
  const reopened = renderHook(() => useReviewSession(saved, vi.fn()), {
    wrapper: wrapper(),
  });
  for (const id of ["a", "b"]) {
    act(() => reopened.result.current.setActive(id));
    const { text, current } = reopened.result.current;
    const range = current && decisionRange(text, current);
    expect(range && text.slice(range.start, range.end)).toBe("对");
  }
});

test("adjusting the viewing crop never replaces source evidence or submits a final decision", async () => {
  const fixture = issueFixture();
  const issue = {
    ...fixture,
    locations: fixture.locations?.map<components["schemas"]["ReviewLocation"]>(
      (row) =>
        row.id === "a"
          ? { ...row, boxes: [[0.1, 0.2, 0.3, 0.4]], image_precision: "line" }
          : row,
    ),
  };
  const put = vi
    .spyOn(client, "PUT")
    .mockImplementation(async (_p, options) => ({
      data: {
        revision: 2,
        draft_text: options!.body.text,
        resolutions: options!.body.resolutions,
      },
      response: new Response(),
    }));
  const post = vi.spyOn(client, "POST");
  const session = renderHook(() => useReviewSession(issue, vi.fn()), {
    wrapper: wrapper(),
  });
  act(() => session.result.current.setBoxes({ a: [0.5, 0.6, 0.7, 0.8] }));
  expect(session.result.current.current?.boxes).toEqual([[0.5, 0.6, 0.7, 0.8]]);
  await act(() => session.result.current.finish("keep"));
  const rows = put.mock.calls.at(-1)?.[1]?.body.locations;
  expect(
    rows?.map((row) => ({
      id: row.id,
      boxes: row.boxes,
      hash: row.image_sha256,
    })),
  ).toEqual(
    issue.locations?.map((row) => ({
      id: row.id,
      boxes: row.boxes,
      hash: row.image_sha256,
    })),
  );
  expect(session.result.current.ready).toBe(false);
  expect(post).not.toHaveBeenCalled();
});

test.each([
  { action: "keep", replacement: "丙对丁" },
  { action: "edit", replacement: "丙对丁" },
  { action: "keep", replacement: "" },
  { action: "edit", replacement: "" },
] as const)(
  "changing or losing a later explicit span revokes its $action judgment ($replacement)",
  async ({ action, replacement }) => {
    const fixture = issueFixture();
    const [first, second] = fixture.locations ?? [];
    if (!first || !second)
      throw new Error("This fixture requires two source locations");
    const issue: components["schemas"]["ReviewIssue"] = {
      ...fixture,
      text: fixture.text + "独立段落。",
      locations: [
        { ...first, text_spans: [first, second] },
        {
          ...second,
          id: "other",
          anchor: { exact: "独立段落", prefix: "", suffix: "。" },
          focus: "独立",
          candidate: "独立段落",
        },
      ],
    };
    vi.spyOn(client, "PUT").mockImplementation(async (_p, options) => ({
      data: {
        revision: 2,
        draft_text: options!.body.text,
        resolutions: options!.body.resolutions,
        locations: options!.body.locations,
      },
      response: new Response(),
    }));
    const session = renderHook(() => useReviewSession(issue, vi.fn()), {
      wrapper: wrapper(),
    });
    await act(() => session.result.current.finish(action));
    act(() => session.result.current.setActive("other"));
    await act(() => session.result.current.finish("keep"));
    expect(session.result.current.ready).toBe(true);
    act(() =>
      session.result.current.changeText(
        issue.text.replace("丙错丁", replacement),
      ),
    );
    await act(() =>
      session.result.current.saveDraft(session.result.current.text),
    );
    expect(session.result.current.resolutions).toEqual([
      { id: "other", action: "keep" },
    ]);
    expect(session.result.current.ready).toBe(false);
  },
);
