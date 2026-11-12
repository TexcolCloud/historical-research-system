// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ArgumentsPage } from "./StudyRecords";
import { TopicTimelinePage } from "./StudyTimeline";

vi.mock("./StudyTopics", () => ({
  MaterialPreview: ({
    materialId,
    onClose,
  }: {
    materialId: string;
    onClose: () => void;
  }) => (
    <div>
      原文依据 {materialId}
      <button onClick={onClose}>收起依据</button>
    </div>
  ),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

test("timeline date basis waits for Apply with the rest of the draft filters", async () => {
  const topic = {
    id: "t1",
    title: "专题",
    materials: [],
    book_ids: [],
    version: 1,
    archived: false,
  };
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/timeline?"))
      return Response.json({
        items: [],
        total: 0,
        dated_total: 0,
        undated_total: 0,
        offset: 0,
        limit: 20,
      });
    if (url.endsWith("/topics/t1")) return Response.json(topic);
    return Response.json({ items: [topic], total: 1, offset: 0, limit: 100 });
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <MemoryRouter initialEntries={["/timeline?topic=t1"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <TopicTimelinePage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("0 条线索");
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "日期依据" }),
    "formation",
  );
  expect(
    fetcher.mock.calls
      .filter(([input]) => String(input).includes("/timeline?"))
      .some(([input]) => String(input).includes("date_basis=formation")),
  ).toBe(false);
  expect(screen.getByText("筛选条件尚未应用")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "应用时间筛选" }));
  await screen.findByText("文献形成日期不代表材料叙述的事项发生日期。");
  expect(
    fetcher.mock.calls.some(([input]) =>
      String(input).includes("date_basis=formation"),
    ),
  ).toBe(true);
});

test("formation documents have no event-review badge while event plans and conditions keep their meaning", async () => {
  const topic = {
    id: "t1",
    title: "专题",
    materials: [],
    book_ids: [],
    version: 1,
    archived: false,
  };
  const document = {
    id: "document",
    name: "已核验来函",
    description: "文献原文摘要",
    item_kind: "document",
    date_basis: "formation",
    event_status: "unknown",
    date: { text: "1948年", certainty: "stated" },
    undated: false,
    card_id: "card",
    card_version: 1,
    book_id: "book",
    evidence_ids: [],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/timeline?")) {
        const items = url.includes("date_basis=formation")
          ? [document]
          : [
              {
                ...document,
                id: "planned",
                name: "拟议行动",
                item_kind: "event",
                date_basis: "event",
                event_status: "planned",
              },
              {
                ...document,
                id: "conditional",
                name: "有条件行动",
                item_kind: "event",
                date_basis: "event",
                event_status: "conditional",
              },
            ];
        return Response.json({
          items,
          total: items.length,
          dated_total: items.length,
          undated_total: 0,
          offset: 0,
          limit: 20,
        });
      }
      if (url.endsWith("/cards/card"))
        return Response.json({
          id: "card",
          version: 1,
          title: document.name,
          book_id: "book",
          evidence: [],
        });
      if (url.endsWith("/topics/t1")) return Response.json(topic);
      return Response.json({ items: [topic], total: 1 });
    }),
  );
  render(
    <MemoryRouter
      initialEntries={[
        "/timeline?topic=t1&date_basis=formation&selected=document",
      ]}
    >
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <TopicTimelinePage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: document.name });
  expect(screen.queryByText("发生性质尚未核对")).toBeNull();
  expect(screen.getByText("该日期不代表事项发生日期。")).toBeTruthy();
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "日期依据" }),
    "event",
  );
  await userEvent.click(screen.getByRole("button", { name: "应用时间筛选" }));
  expect(await screen.findByText("计划或拟议事项")).toBeTruthy();
  expect(screen.getByText("附带条件的事项")).toBeTruthy();
});

test("research records default to every kind and keep the list beside one selected full document", async () => {
  const topic = {
    id: "t1",
    title: "专题",
    materials: [],
    book_ids: [],
    version: 1,
    archived: false,
  };
  const records = [
    {
      id: "r1",
      topic_id: "t1",
      kind: "note",
      title: "第一条札记",
      text: "第一条完整正文",
      limitations: "",
      source_refs: [],
      version: 1,
    },
    {
      id: "r2",
      topic_id: "t1",
      kind: "question",
      title: "第二条问题",
      text: "第二条完整正文",
      limitations: "",
      source_refs: [],
      version: 1,
    },
  ];
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/records?"))
      return Response.json({ items: records, total: 2, offset: 0, limit: 20 });
    if (url.endsWith("/topics/t1")) return Response.json(topic);
    return Response.json({ items: [topic], total: 1, offset: 0, limit: 100 });
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <MemoryRouter initialEntries={["/arguments?topic=t1"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ArgumentsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const list = await screen.findByRole("navigation", { name: "研究记录清单" });
  const detail = screen.getByRole("region", { name: "当前研究记录" });
  expect(
    (
      screen.getByRole("combobox", {
        name: "记录类型筛选",
      }) as HTMLSelectElement
    ).value,
  ).toBe("all");
  expect(within(list).getByRole("button", { name: /第二条问题/ })).toBeTruthy();
  expect(within(detail).getByText("第一条完整正文")).toBeTruthy();
  expect(within(detail).queryByText("第二条完整正文")).toBeNull();
  await userEvent.click(
    within(list).getByRole("button", { name: /第二条问题/ }),
  );
  expect(within(detail).getByText("第二条完整正文")).toBeTruthy();
  expect(
    fetcher.mock.calls.some(([url]) => String(url).includes("kind=argument")),
  ).toBe(false);
});

test("a record return link opens its exact document even beyond the current list page", async () => {
  const topic = {
    id: "t1",
    title: "专题",
    materials: [],
    book_ids: [],
    version: 1,
    archived: false,
  };
  const record = {
    id: "outside-page",
    topic_id: "t1",
    kind: "note",
    title: "返回的精确札记",
    text: "保留完整原文与解释。".repeat(120),
    limitations: "",
    source_refs: [],
    version: 5,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/records/outside-page")) return Response.json(record);
      if (url.includes("/records?"))
        return Response.json({ items: [], total: 45, offset: 0, limit: 20 });
      if (url.endsWith("/topics/t1")) return Response.json(topic);
      return Response.json({ items: [topic], total: 1, offset: 0, limit: 100 });
    }),
  );
  render(
    <MemoryRouter initialEntries={["/arguments?topic=t1&record=outside-page"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ArgumentsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: record.title });
  await userEvent.click(screen.getByRole("button", { name: "编辑记录" }));
  expect(
    (screen.getByRole("textbox", { name: "记录正文" }) as HTMLTextAreaElement)
      .value,
  ).toBe(record.text);
});

test("record evidence restores from its return URL beside the document instead of below its body", async () => {
  const topic = {
    id: "t1",
    title: "专题",
    materials: [{ id: "m1", title: "依据一", source_current: true }],
    book_ids: [],
    version: 1,
    archived: false,
  };
  const record = {
    id: "r1",
    topic_id: "t1",
    kind: "note",
    title: "待核对札记",
    text: "长篇个人整理。".repeat(200),
    limitations: "",
    source_refs: ["m1"],
    version: 1,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/records?"))
        return Response.json({
          items: [record],
          total: 1,
          offset: 0,
          limit: 20,
        });
      if (url.endsWith("/topics/t1")) return Response.json(topic);
      return Response.json({ items: [topic], total: 1 });
    }),
  );
  render(
    <MemoryRouter initialEntries={["/arguments?topic=t1&record=r1&source=m1"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ArgumentsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const evidence = await screen.findByRole("complementary", {
    name: "当前记录的原文依据",
  });
  expect(within(evidence).getByText("原文依据 m1")).toBeTruthy();
  expect(
    within(screen.getByRole("region", { name: "当前研究记录" })).queryByText(
      "原文依据 m1",
    ),
  ).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "收起依据" }));
  expect(
    screen.queryByRole("complementary", { name: "当前记录的原文依据" }),
  ).toBeNull();
  expect(screen.getByRole("heading", { name: "待核对札记" })).toBeTruthy();
});
