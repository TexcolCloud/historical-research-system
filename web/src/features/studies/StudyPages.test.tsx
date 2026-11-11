// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import {
  TopicsPage,
  ArgumentsPage,
  TopicTimelinePage,
  AnalysisPage,
} from "./StudyPages";
import { RecordEditor } from "./StudyRecords";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

test("returning to a record list retains the requested kind and page before server pagination", async () => {
  const topic = {
    id: "topic",
    title: "专题",
    description: "",
    materials: [],
    book_ids: [],
    version: 1,
    archived: false,
  };
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/records?"))
      return Response.json({ items: [], total: 41, offset: 20, limit: 20 });
    if (url.endsWith("/topics/topic")) return Response.json(topic);
    return Response.json({ items: [topic], total: 1, offset: 0, limit: 100 });
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <MemoryRouter
      initialEntries={["/arguments?topic=topic&kind=note&offset=20"]}
    >
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ArgumentsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("21–40 / 41");
  const urls = fetcher.mock.calls
    .map(([input]) => String(input))
    .filter((url) => url.includes("/records?"));
  expect(urls).toHaveLength(1);
  expect(urls[0]).toContain("offset=20");
  expect(urls[0]).toContain("kind=note");
});

test("a refreshed personal draft restores its original expected version instead of silently overwriting a newer record", async () => {
  const topic = {
    id: "topic",
    title: "专题",
    description: "",
    book_ids: [],
    materials: [],
    version: 1,
    archived: false,
    created_at: 1,
    updated_at: 1,
  };
  const record = {
    id: "note",
    topic_id: "topic",
    kind: "note" as const,
    title: "已存记录",
    text: "",
    limitations: "",
    source_refs: [],
    version: 2,
    archived: false,
    created_at: 1,
    updated_at: 1,
  };
  const cache = new QueryClient();
  cache.setQueryData(["platform", "runtime"], { workspace_id: "workspace-a" });
  const fetcher = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      Response.json({ detail: "记录已有新版本。" }, { status: 409 }),
  );
  vi.stubGlobal("fetch", fetcher);
  const first = render(
    <MemoryRouter>
      <QueryClientProvider client={cache}>
        <RecordEditor
          topic={topic}
          record={record}
          onSaved={vi.fn()}
          onCancel={vi.fn()}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.clear(screen.getByRole("textbox", { name: "记录标题" }));
  await userEvent.type(
    screen.getByRole("textbox", { name: "记录标题" }),
    "本机未提交草稿",
  );
  first.unmount();
  render(
    <MemoryRouter>
      <QueryClientProvider client={cache}>
        <RecordEditor
          topic={topic}
          record={{ ...record, version: 3 }}
          onSaved={vi.fn()}
          onCancel={vi.fn()}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(
    (screen.getByRole("textbox", { name: "记录标题" }) as HTMLInputElement)
      .value,
  ).toBe("本机未提交草稿");
  await userEvent.click(screen.getByRole("button", { name: "保存记录" }));
  await screen.findByText("记录已有新版本。");
  expect(
    JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body)).expected_version,
  ).toBe(2);
});

test("an unverified AI suggestion cannot be adopted as a personal record", async () => {
  const topic = {
    id: "topic",
    title: "专题",
    materials: [{ id: "m1", title: "原文", source_current: true }],
    book_ids: [],
    description: "",
    version: 1,
    archived: false,
  };
  const analysis = {
    id: "a1",
    topic_id: "topic",
    mode: "summary",
    state: "ready",
    version: 2,
    material_ids: ["m1"],
    prompt: "",
    suggestions: [
      {
        id: "s1",
        title: "需要复核的建议",
        text: "引文待核",
        limitations: "",
        verified: false,
        evidence_refs: [],
      },
    ],
  };
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/analyses/a1")) return Response.json(analysis);
    if (url.includes("/analyses?"))
      return Response.json({
        items: [analysis],
        total: 1,
        offset: 0,
        limit: 100,
      });
    if (url.endsWith("/topics/topic")) return Response.json(topic);
    return Response.json({ items: [topic], total: 1, offset: 0, limit: 100 });
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <MemoryRouter initialEntries={["/analysis?topic=topic&analysis=a1"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <AnalysisPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: "需要复核的建议" });
  expect(
    (screen.getByRole("button", { name: "采纳为札记" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(
    fetcher.mock.calls.every(([url]) => !String(url).includes("/adopt")),
  ).toBe(true);
});
test("creating a topic sends a personal title and opens the saved topic without source approval", async () => {
  const saved = {
    id: "topic",
    title: "交通网络研究",
    description: "",
    book_ids: [],
    materials: [],
    version: 1,
    archived: false,
  };
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") return Response.json(saved);
      if (url.includes("/topics/topic")) return Response.json(saved);
      return Response.json({ items: [], total: 0, offset: 0, limit: 100 });
    },
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <TopicsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(screen.getByRole("button", { name: "新建专题" }));
  await userEvent.type(
    screen.getByRole("textbox", { name: "专题名称" }),
    "交通网络研究",
  );
  await userEvent.click(screen.getByRole("button", { name: "创建专题" }));
  expect(
    await screen.findByRole("heading", { name: "交通网络研究" }),
  ).toBeTruthy();
  const posts = fetcher.mock.calls.filter(
    ([, init]) => init?.method === "POST",
  );
  expect(posts).toHaveLength(1);
  expect(JSON.parse(String(posts[0][1]?.body))).toEqual({
    title: "交通网络研究",
    description: "",
    book_ids: [],
  });
});

test("topic chronology labels a personal relative-date clue without fabricating a calendar date", async () => {
  const topic = {
    id: "topic",
    title: "专题",
    description: "",
    book_ids: [],
    materials: [],
    version: 1,
    archived: false,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/timeline?"))
        return Response.json({
          items: [
            {
              id: "clue",
              item_kind: "personal",
              personal_record_id: "record",
              name: "核对相对日期",
              description: "原文只说次月",
              date: { text: "次月", normalized: null, certainty: "unknown" },
              undated: true,
              event_status: "planned",
              status_note: "个人补记，不改写史料日期。",
              source_refs: [],
            },
          ],
          total: 1,
          dated_total: 0,
          undated_total: 1,
          offset: 0,
          limit: 20,
          date_basis: "event",
          truncated: false,
        });
      if (url.endsWith("/topics/topic")) return Response.json(topic);
      return Response.json({ items: [topic], total: 1, offset: 0, limit: 100 });
    }),
  );
  render(
    <MemoryRouter initialEntries={["/timeline?topic=topic"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <TopicTimelinePage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(
    await screen.findByRole("button", { name: "核对相对日期" }),
  ).toBeTruthy();
  expect(screen.getByText("个人补记")).toBeTruthy();
  expect(screen.getByText("未定年")).toBeTruthy();
  expect(screen.getByText("次月")).toBeTruthy();
  expect(screen.queryByText("1948-01-01")).toBeNull();
});

test("a failed argument save preserves the draft and selected source material", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const topic = {
        id: "topic",
        title: "专题",
        description: "",
        book_ids: [],
        materials: [{ id: "m1", title: "已核验来函", source_current: true }],
        version: 1,
        archived: false,
      };
      if (init?.method === "POST")
        return Response.json(
          { detail: "来源已有更新，请核对引用。" },
          { status: 409 },
        );
      if (String(input).endsWith("/topics/topic")) return Response.json(topic);
      return Response.json({
        items: String(input).includes("/records") ? [] : [topic],
        total: 1,
        offset: 0,
        limit: 100,
      });
    }),
  );
  render(
    <MemoryRouter initialEntries={["/arguments?topic=topic"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ArgumentsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "新建论据" }),
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "记录标题" }),
    "交通与组织动员",
  );
  await userEvent.click(screen.getByRole("checkbox", { name: "已核验来函" }));
  await userEvent.click(screen.getByRole("button", { name: "保存记录" }));
  expect(await screen.findByText("来源已有更新，请核对引用。")).toBeTruthy();
  expect(
    (screen.getByRole("textbox", { name: "记录标题" }) as HTMLInputElement)
      .value,
  ).toBe("交通与组织动员");
  expect(
    (screen.getByRole("checkbox", { name: "已核验来函" }) as HTMLInputElement)
      .checked,
  ).toBe(true);
});
