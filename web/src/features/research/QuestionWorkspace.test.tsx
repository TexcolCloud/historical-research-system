// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import QuestionWorkspace from "./QuestionWorkspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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
test("a research question persists notes through a version conflict instead of discarding the local draft", async () => {
  const question = {
    id: "question",
    title: "拨款是否落实",
    book_ids: [],
    notes: "已有笔记",
    status: "open",
    version: 1,
    definition_version: 1,
    selections: [],
    findings: [],
    runs: [],
  };
  let patch: unknown;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "PATCH") {
        patch = JSON.parse(String(init.body));
        return Response.json(
          { detail: "问题已经更新，请重新读取。" },
          { status: 409 },
        );
      }
      return Response.json(
        url.includes("/questions?")
          ? { items: [question], total: 1, offset: 0, limit: 100 }
          : question,
      );
    }),
  );
  render(
    <MemoryRouter
      initialEntries={["/research?view=questions&question=question"]}
    >
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <QuestionWorkspace />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const notes = await screen.findByRole("textbox", { name: "研究笔记" });
  await userEvent.clear(notes);
  await userEvent.type(notes, "保留我的新判断");
  await userEvent.click(screen.getByRole("button", { name: "保存问题与笔记" }));
  await screen.findByText("问题已经更新，请重新读取。");
  expect(patch).toMatchObject({ expected_version: 1, notes: "保留我的新判断" });
  expect((notes as HTMLTextAreaElement).value).toBe("保留我的新判断");
});

test("stopped research with pending and blocked runs remains actionable without submitting machine status as a user decision", async () => {
  const question = {
    id: "question",
    title: "查证拨款",
    book_ids: [],
    notes: "",
    status: "ready",
    version: 3,
    definition_version: 1,
    selections: [],
    findings: [],
    runs: [
      { run_id: "pending-run", state: "pending", book_id: "book" },
      { run_id: "blocked-run", state: "technical_blocked", book_id: "book" },
    ],
  };
  let body: Record<string, unknown> | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PATCH") body = JSON.parse(String(init.body));
      return Response.json(
        String(input).includes("/questions?")
          ? { items: [question], total: 1, offset: 0, limit: 100 }
          : question,
      );
    }),
  );
  render(
    <MemoryRouter
      initialEntries={["/research?view=questions&question=question"]}
    >
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <QuestionWorkspace />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByRole("textbox", { name: "研究笔记" });
  expect(screen.queryByText("查证结果已保存")).toBeNull();
  expect(await screen.findByText("需要人工核对")).toBeTruthy();
  expect(await screen.findByText("技术阻塞，需要恢复")).toBeTruthy();
  expect(screen.queryByRole("option", { name: "查证结果已保存" })).toBeNull();
  await userEvent.type(
    screen.getByRole("textbox", { name: "研究笔记" }),
    "补充笔记",
  );
  await userEvent.click(screen.getByRole("button", { name: "保存问题与笔记" }));
  expect(body?.notes).toBe("补充笔记");
  expect(body).not.toHaveProperty("status");
  expect(
    screen
      .getAllByRole("link", { name: "查看任务与恢复操作" })[0]
      .getAttribute("href"),
  ).toContain("researchRun=pending-run");
});
