// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import QuestionWorkspace from "./QuestionWorkspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("historical research records remain readable and exportable without new research or editing actions", async () => {
  const question = {
    id: "q",
    title: "此前的研究问题",
    book_ids: [],
    notes: "我此前的人工笔记",
    status: "ready",
    version: 7,
    definition_version: 2,
    selections: [],
    findings: [],
    runs: [],
  };
  const request = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json(
      String(input).includes("/questions?")
        ? { items: [question], total: 1, offset: 0, limit: 100 }
        : question,
    ),
  );
  vi.stubGlobal("fetch", request);
  render(
    <MemoryRouter initialEntries={["/research?view=questions&question=q"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <QuestionWorkspace />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: "此前的研究问题" });
  expect(screen.queryByRole("button", { name: "继续查证" })).toBeNull();
  expect(screen.queryByRole("button", { name: "新建研究问题" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "研究笔记" })).toBeNull();
  expect(screen.getByText("我此前的人工笔记")).toBeTruthy();
  const exported = screen
    .getByRole("link", { name: "导出历史记录 Markdown" })
    .getAttribute("href")!;
  expect(decodeURIComponent(exported)).toContain("我此前的人工笔记");
  expect(decodeURIComponent(exported)).toContain('"version": 7');
  expect(
    request.mock.calls.every(
      (call) => !call[1]?.method || call[1].method === "GET",
    ),
  ).toBe(true);
});

test("derived record changes preserve selection decisions and valid source links without claiming the source is outdated", async () => {
  const target = {
    chapter_id: "chapter",
    run_id: "run",
    physical_page: 3,
    start: 1,
    end: 5,
    text_sha256: "a".repeat(64),
    image_sha256: "b".repeat(64),
    anchor: "原文",
  };
  const selections = (
    [
      ["card", "adopted", "卡片选材"],
      ["entity", "excluded", "人物选材"],
      ["relation", "saved", "关系选材"],
    ] as const
  ).map(([kind, decision, title]) => ({
    id: kind,
    kind,
    target_id: kind,
    title,
    decision,
    note: "保留选材说明",
    record_current: false,
    card_current: false,
    source_current: true,
    ...(kind === "entity"
      ? {}
      : {
          record_ref: { kind, id: kind, version: 2, sha256: "c".repeat(64) },
          version_refs: [{ card_id: "card", card_version: 2 }],
        }),
    evidence: [
      {
        quote: `${kind}保留原文`,
        source_current: true,
        book_id: "book",
        reading_target: target,
      },
    ],
  }));
  const question = {
    id: "q",
    title: "选材记录换版",
    book_ids: [],
    notes: "",
    status: "open",
    version: 3,
    definition_version: 1,
    findings: [],
    runs: [],
    selections: [
      ...selections,
      {
        id: "source",
        kind: "source",
        target_id: "chapter",
        title: "原文选材",
        decision: "saved",
        note: "",
        source_current: true,
        evidence: [
          {
            quote: "source保留原文",
            source_current: true,
            book_id: "book",
            reading_target: target,
          },
        ],
      },
    ],
  };
  const request = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) =>
    Response.json(
      String(input).includes("/questions?")
        ? { items: [question], total: 1, offset: 0, limit: 100 }
        : question,
    ),
  );
  vi.stubGlobal("fetch", request);
  render(
    <MemoryRouter initialEntries={["/research?view=questions&question=q"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <QuestionWorkspace />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const notice =
    "此条卡片、人物或关系已有变化，保留的是当时的选材记录，请核对当前版本。";
  expect(await screen.findAllByText(notice)).toHaveLength(3);
  for (const selection of selections) {
    const article = screen.getByText(selection.title).closest("article")!;
    expect(article.getAttribute("data-decision")).toBe(selection.decision);
    expect(
      within(article)
        .getByRole("link", { name: "核对原文证据 1" })
        .getAttribute("href"),
    ).toContain("textSha=" + "a".repeat(64));
    expect(
      within(article).queryByText(
        "选材依据已有新版本，当前保留旧引文；重新核验后再引用。",
      ),
    ).toBeNull();
  }
  const source = screen.getByText("原文选材").closest("article")!;
  expect(within(source).queryByText(notice)).toBeNull();
  expect(
    request.mock.calls.every(
      (call) => !call[1]?.method || call[1].method === "GET",
    ),
  ).toBe(true);
});

test("the question list consumes lightweight summaries without assuming evidence arrays are present", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        items: [
          {
            id: "q",
            title: "轻量问题摘要",
            book_ids: [],
            notes: "",
            status: "open",
            version: 1,
            definition_version: 1,
            selection_count: 3,
          },
        ],
        total: 1,
        offset: 0,
        limit: 100,
      }),
    ),
  );
  render(
    <MemoryRouter initialEntries={["/research?view=questions"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <QuestionWorkspace />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(
    await screen.findByRole("button", { name: /轻量问题摘要/ }),
  ).toBeTruthy();
  expect(screen.getByText("待研究 · 3 项材料")).toBeTruthy();
});

test("retained selections and findings show their authoritative per-source or card revision state", async () => {
  const question = {
    id: "q",
    title: "旧引文核查",
    book_ids: [],
    notes: "",
    status: "ready",
    version: 3,
    definition_version: 1,
    runs: [],
    selections: [
      {
        id: "selection",
        kind: "card",
        target_id: "card",
        title: "旧卡摘录",
        decision: "adopted",
        note: "",
        card_current: true,
        evidence: [
          {
            quote: "选材保留引文",
            source_current: false,
            reading_target: null,
          },
        ],
      },
    ],
    findings: [
      {
        id: "finding",
        text: "原有模型结论",
        status: "answered",
        unresolved: [],
        definition_version: 1,
        evidence_ids: ["e"],
        card_id: "old-card",
        card_current: false,
        evidence: [{ quote: "先前依据", source_current: true }],
      },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      Response.json(
        String(input).includes("/questions?")
          ? { items: [question], total: 1, offset: 0, limit: 100 }
          : question,
      ),
    ),
  );
  render(
    <MemoryRouter initialEntries={["/research?view=questions&question=q"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <QuestionWorkspace />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(
    await screen.findByText(
      "选材依据已有新版本，当前保留旧引文；重新核验后再引用。",
    ),
  ).toBeTruthy();
  expect(
    screen.getByText(
      "结论所依赖的卡片或原文已有新版本，当前保留历史查证结果。",
    ),
  ).toBeTruthy();
  expect(screen.getByText("选材保留引文")).toBeTruthy();
  expect(screen.queryByRole("link", { name: "核对原文证据 1" })).toBeNull();
  expect(screen.queryByText("已人工确认")).toBeNull();
});
