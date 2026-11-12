// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import StudyCanvas from "./StudyCanvas";
import { CanvasConnectionEditor } from "./canvas-inspector";
import type { StudyCanvasRecord, StudyRecord, StudyTopic } from "./types";
import type { ReactFlowProps } from "@xyflow/react";

const { flow } = vi.hoisted(() => ({ flow: vi.fn() }));

vi.mock("@xyflow/react", () => ({
  ReactFlow: (props: ReactFlowProps) => {
    flow(props);
    return (
      <div aria-label="画布视图">
        {props.nodes?.map((node) => (
          <button
            key={node.id}
            onClick={() => props.onNodeClick?.({} as never, node)}
          >
            {String(node.data.title)}
          </button>
        ))}
        {props.children}
      </div>
    );
  },
  Background: () => null,
  Controls: () => null,
  MiniMap: () => null,
  NodeResizer: () => null,
  Handle: () => null,
  Position: { Left: "left", Right: "right" },
  MarkerType: { ArrowClosed: "arrowclosed" },
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

const topic: StudyTopic = {
  id: "t",
  title: "研究专题",
  description: "",
  book_ids: ["book"],
  archived: false,
  version: 1,
  created_at: 1,
  updated_at: 1,
  materials: [
    {
      id: "m",
      card_id: "card",
      card_version: 3,
      book_id: "book",
      article_id: "article",
      title: "会议记录",
      source_run_id: "run",
      publication_sha256: "p",
      article_sha256: "a",
      evidence_ids: ["e"],
      source_current: true,
      source_citation: "会议记录",
      printed_pages: "",
      citation_completeness: "partial",
      preview: "原文片段预览",
      preview_truncated: false,
      authors: "",
      formation_date_text: "",
    },
  ],
};
const canvas: StudyCanvasRecord = {
  id: "c",
  topic_id: "t",
  title: "材料画布",
  version: 7,
  archived: false,
  created_at: 1,
  updated_at: 1,
  document: { nodes: [], edges: [], viewport: { x: 0, y: 0, z: 1 } },
};

function openCanvas(
  onPatch: (body: Record<string, unknown>) => Response,
  initial = canvas,
  contextRecords: StudyRecord[] = [],
  fullRecord?: () => Promise<Response>,
  drawerRecords: StudyRecord[] = [],
  onImport?: (body: Record<string, unknown>) => Response,
) {
  const fetch = vi.fn(async (url: RequestInfo | URL, options?: RequestInit) => {
    const path = String(url);
    if (options?.method === "PATCH")
      return onPatch(JSON.parse(String(options.body)));
    if (
      options?.method === "POST" &&
      path.endsWith("/canvases/import") &&
      onImport
    )
      return onImport(JSON.parse(String(options.body)));
    if (path === "/api/v2/runtime")
      return Response.json({ workspace_id: "w", validation_scope: "content" });
    if (path === "/api/v2/studies/preferences")
      return Response.json({
        version: 1,
        compact: false,
        restore_last_canvas: true,
      });
    if (path === "/api/v2/studies/topics/t") return Response.json(topic);
    if (path === "/api/v2/studies/topics/t/materials/m")
      return Response.json({ ...topic.materials[0], evidence: [] });
    if (path.includes("/topics/t/records"))
      return Response.json({
        items: drawerRecords,
        total: drawerRecords.length,
        offset: 0,
        limit: 200,
      });
    if (path.includes("/topics/t/canvases"))
      return Response.json({
        items: [initial],
        total: 1,
        offset: 0,
        limit: 100,
      });
    if (path === "/api/v2/studies/canvases/c") return Response.json(initial);
    if (path.includes("/canvases/c/context"))
      return Response.json({
        canvas_id: initial.id,
        canvas_version: initial.version,
        topic_id: "t",
        records: contextRecords.map((record) => ({
          ...record,
          text: "已截短的展示文本",
          preview_only: true,
          text_truncated: true,
        })),
        materials: topic.materials,
      });
    if (path.startsWith("/api/v2/studies/records/"))
      return fullRecord
        ? fullRecord()
        : Response.json(
            contextRecords.find((record) => path.endsWith(record.id)),
          );
    throw new Error(`Unexpected request ${path}`);
  });
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter initialEntries={["/studies/t/canvas?canvas=c"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <StudyCanvas topicId="t" canvasId="c" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return fetch;
}

test("canvas navigation retains the same topic when opening timeline and records", async () => {
  openCanvas(() => Response.json(canvas));
  const navigation = await screen.findByRole("navigation", {
    name: "专题内导航",
  });
  const timeline = Array.from(navigation.querySelectorAll("a")).find(
    (link) => link.textContent === "时间线索",
  );
  expect(timeline?.getAttribute("href")).toBe("/timeline?topic=t");
  expect(navigation.querySelector('[aria-current="page"]')?.textContent).toBe(
    "关系画布",
  );
});

test("infrequent backup controls belong to canvas management instead of the primary toolbar", async () => {
  openCanvas(() => Response.json(canvas));
  const backup = await screen.findByLabelText("恢复画布备份");
  expect(backup.closest("details")?.querySelector("summary")?.textContent).toBe(
    "画布管理",
  );
});

test("placing a source saves a membership reference with the canvas version, without copying source text", async () => {
  const patches: Record<string, unknown>[] = [];
  openCanvas((body) => {
    patches.push(body);
    return Response.json({ ...canvas, version: 8, document: body.document });
  });
  await userEvent.click(
    await screen.findByRole("button", { name: "放入画布 会议记录" }),
  );
  await waitFor(() => expect(patches).toHaveLength(1), { timeout: 2500 });
  expect(patches[0].expected_version).toBe(7);
  expect(patches[0].document).toMatchObject({
    nodes: [{ record_kind: "source", record_id: "m" }],
  });
  expect(JSON.stringify(patches[0])).not.toContain("publication_sha256");
  await screen.findByText("已保存");
});

test("sequential drawer additions occupy separate visible rectangles in the current viewport", async () => {
  const records: StudyRecord[] = (["clue", "question"] as const).map(
    (kind) => ({
      id: kind,
      topic_id: "t",
      kind,
      title: kind === "clue" ? "时间线索" : "研究问题",
      text: "个人记录",
      limitations: "",
      source_refs: [],
      archived: false,
      version: 1,
      created_at: 1,
      updated_at: 1,
    }),
  );
  openCanvas(
    (body) => Response.json({ ...canvas, version: 8, document: body.document }),
    canvas,
    [],
    undefined,
    records,
  );
  await screen.findByRole("button", { name: "放入画布 时间线索" });
  const surface = screen.getByRole("region", { name: "自由研究画布" });
  vi.spyOn(surface.parentElement!, "getBoundingClientRect").mockReturnValue(
    new DOMRect(0, 0, 1440, 800),
  );
  const props = flow.mock.lastCall![0] as ReactFlowProps;
  act(() =>
    props.onInit?.({
      screenToFlowPosition: ({ x, y }: { x: number; y: number }) => ({
        x: (x - 40) / 0.8,
        y: (y - 20) / 0.8,
      }),
      fitView: vi.fn(),
    } as never),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "放入画布 时间线索" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "放入画布 研究问题" }),
  );
  const nodes = (flow.mock.lastCall![0] as ReactFlowProps).nodes!;
  expect(nodes).toHaveLength(2);
  const [a, b] = nodes;
  expect(
    a.position.x + a.width! <= b.position.x ||
      b.position.x + b.width! <= a.position.x ||
      a.position.y + a.height! <= b.position.y ||
      b.position.y + b.height! <= a.position.y,
  ).toBe(true);
  for (const node of nodes) {
    expect(node.position.x).toBeGreaterThanOrEqual((320 - 40) / 0.8);
    expect(node.position.x + node.width!).toBeLessThanOrEqual(
      (1440 - 380 - 40) / 0.8,
    );
    expect(node.position.y).toBeGreaterThanOrEqual((32 - 20) / 0.8);
    expect(node.position.y + node.height!).toBeLessThanOrEqual(
      (800 - 32 - 20) / 0.8,
    );
  }
});

test("an intentional drop keeps its exact coordinates even over an existing card", async () => {
  const old = {
    id: "old",
    record_kind: "source" as const,
    record_id: "previous",
    title: "已放置的卡片",
    x: 450,
    y: 250,
    width: 280,
    height: 250,
  };
  const initial: StudyCanvasRecord = {
    ...canvas,
    document: { ...canvas.document, nodes: [old] },
  };
  openCanvas(
    (body) =>
      Response.json({ ...initial, version: 8, document: body.document }),
    initial,
  );
  await screen.findByRole("button", { name: "放入画布 会议记录" });
  const props = flow.mock.lastCall![0] as ReactFlowProps;
  act(() =>
    props.onInit?.({
      screenToFlowPosition: (point: { x: number; y: number }) => point,
      fitView: vi.fn(),
    } as never),
  );
  const dropped = new Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperties(dropped, {
    clientX: { value: 450 },
    clientY: { value: 250 },
    dataTransfer: {
      value: { getData: () => JSON.stringify({ kind: "source", id: "m" }) },
    },
  });
  fireEvent(screen.getByRole("region", { name: "自由研究画布" }), dropped);
  const nodes = (flow.mock.lastCall![0] as ReactFlowProps).nodes!;
  expect(nodes).toHaveLength(2);
  expect(nodes.map((node) => node.position)).toEqual([
    { x: 450, y: 250 },
    { x: 450, y: 250 },
  ]);
});

test("a completed drag saves final geometry as one undoable command and advances the expected version", async () => {
  const initial: StudyCanvasRecord = {
    ...canvas,
    document: {
      ...canvas.document,
      nodes: [
        {
          id: "n",
          record_kind: "source",
          record_id: "m",
          x: 350,
          y: 100,
          width: 280,
          height: 250,
        },
      ],
    },
  };
  const patches: Record<string, unknown>[] = [];
  openCanvas((body) => {
    patches.push(body);
    return Response.json({
      ...initial,
      version: 7 + patches.length,
      document: body.document,
    });
  }, initial);
  await screen.findByRole("button", { name: "会议记录" });
  let props = flow.mock.lastCall![0] as ReactFlowProps;
  const node = props.nodes![0];
  act(() => props.onNodeDragStart?.({} as never, node, [node]));
  act(() =>
    props.onNodesChange?.([
      {
        id: "n",
        type: "position",
        position: { x: 470, y: 230 },
        dragging: true,
      },
    ]),
  );
  props = flow.mock.lastCall![0] as ReactFlowProps;
  const final = { ...props.nodes![0], position: { x: 500, y: 240 } };
  act(() => props.onNodeDragStop?.({} as never, final, [final]));
  await waitFor(() => expect(patches).toHaveLength(1), { timeout: 2500 });
  expect(patches[0]).toMatchObject({
    expected_version: 7,
    document: { nodes: [{ x: 500, y: 240 }] },
  });
  await userEvent.click(screen.getByRole("button", { name: "撤销画布操作" }));
  await waitFor(() => expect(patches).toHaveLength(2), { timeout: 2500 });
  expect(patches[1]).toMatchObject({
    expected_version: 8,
    document: { nodes: [{ x: 350, y: 100 }] },
  });
});

test("a version conflict preserves the scoped draft and reopening does not silently retry against a newer version", async () => {
  const patches = vi.fn(() =>
    Response.json(
      { detail: "资料已有新版本，当前输入已保留。" },
      { status: 409 },
    ),
  );
  openCanvas(patches);
  await userEvent.click(
    await screen.findByRole("button", { name: "放入画布 会议记录" }),
  );
  await screen.findByRole("button", { name: "另存当前草稿" });
  const draft = JSON.parse(localStorage.getItem("hrs:study-canvas:w:c")!);
  expect(draft.base_version).toBe(7);
  expect(draft.document.nodes[0].record_id).toBe("m");
  cleanup();
  openCanvas(patches, { ...canvas, version: 8 });
  await screen.findByText(
    "服务器已有新版本，已保留本地布局。请另存草稿，或核对后载入服务器版本。",
  );
  await new Promise((resolve) => setTimeout(resolve, 850));
  expect(patches).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole("button", { name: "定位画布 会议记录" }),
  ).toBeTruthy();
});

test("loading a clean server layout still exposes the retained local draft after reopening", async () => {
  localStorage.setItem(
    "hrs:study-canvas:w:c:retained",
    JSON.stringify({
      workspace_id: "w",
      topic_id: "t",
      canvas_id: "c",
      base_version: 7,
      document: canvas.document,
    }),
  );
  openCanvas(() => Response.json(canvas));
  expect(
    await screen.findByRole("button", { name: "导出此前草稿" }),
  ).toBeTruthy();
});

test("restored backups get a recognizable copy title and explain that personal records were copied", async () => {
  const imports: Record<string, unknown>[] = [];
  openCanvas(
    () => Response.json(canvas),
    canvas,
    [],
    undefined,
    [],
    (body) => {
      imports.push(body);
      return Response.json({
        ...canvas,
        id: "restored",
        title: body.title,
        version: 1,
      });
    },
  );
  await userEvent.click(await screen.findByText("画布管理"));
  await screen.findByRole("button", { name: "恢复备份" });
  const file = new File(
    [JSON.stringify({ canvas: { title: "原有画布" } })],
    "canvas.json",
    { type: "application/json" },
  );
  await userEvent.upload(
    screen.getByLabelText<HTMLInputElement>("恢复画布备份"),
    file,
  );
  await waitFor(() => expect(imports).toHaveLength(1));
  expect(imports[0].title).toBe("原有画布 · 恢复副本");
  await screen.findByText(/个人记录已恢复为副本/);
});

test("connecting a selected group defaults to real cards instead of an unavailable group option", async () => {
  const connect = vi.fn();
  const nodes = ["a", "b", "group"].map((id) => ({
    id,
    record_kind: id === "group" ? ("group" as const) : ("source" as const),
    record_id: id === "group" ? "" : "m",
    x: 0,
    y: 0,
    width: 280,
    height: 250,
  }));
  render(
    <CanvasConnectionEditor
      nodes={nodes}
      labels={new Map()}
      selected={["group"]}
      onConnect={connect}
      onClose={() => {}}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "建立连线" }));
  expect(connect).toHaveBeenCalledWith("a", "b", "association", "关联");
});

test("canvas bindings beyond the drawer page show their own preview and load full text before editing", async () => {
  const record: StudyRecord = {
    id: "outside",
    topic_id: "t",
    kind: "note",
    title: "未分页加载的札记",
    text: "完整正文应当被保留，不能用截短预览覆盖。",
    limitations: "",
    source_refs: [],
    archived: false,
    version: 4,
    created_at: 1,
    updated_at: 1,
  };
  const initial: StudyCanvasRecord = {
    ...canvas,
    document: {
      ...canvas.document,
      nodes: [
        {
          id: "n",
          record_kind: "record",
          record_id: record.id,
          x: 400,
          y: 120,
          width: 280,
          height: 250,
        },
      ],
    },
  };
  openCanvas(() => Response.json(initial), initial, [record]);
  await userEvent.click(
    await screen.findByRole("button", { name: record.title }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "编辑记录" }),
  );
  const text = await screen.findByRole<HTMLTextAreaElement>("textbox", {
    name: "记录正文",
  });
  expect(text.value).toBe(record.text);
});

test("double-clicking an unloaded record waits for the full record instead of creating an empty replacement", async () => {
  const record: StudyRecord = {
    id: "pending",
    topic_id: "t",
    kind: "note",
    title: "等待完整正文的札记",
    text: "完整正文保持原样。",
    limitations: "",
    source_refs: [],
    archived: false,
    version: 4,
    created_at: 1,
    updated_at: 1,
  };
  const initial: StudyCanvasRecord = {
    ...canvas,
    document: {
      ...canvas.document,
      nodes: [
        {
          id: "n",
          record_kind: "record",
          record_id: record.id,
          x: 400,
          y: 120,
          width: 280,
          height: 250,
        },
      ],
    },
  };
  let resolveRecord: (response: Response) => void = () => {};
  const response = new Promise<Response>((resolve) => {
    resolveRecord = resolve;
  });
  openCanvas(
    () => Response.json(initial),
    initial,
    [record],
    () => response,
  );
  await screen.findByRole("button", { name: record.title });
  const props = flow.mock.lastCall![0] as ReactFlowProps;
  act(() => props.onNodeDoubleClick?.({} as never, props.nodes![0]));
  await screen.findByText("正在读取完整记录…");
  expect(screen.queryByRole("textbox", { name: "记录正文" })).toBeNull();
  await act(async () => resolveRecord(Response.json(record)));
  const text = await screen.findByRole<HTMLTextAreaElement>("textbox", {
    name: "记录正文",
  });
  expect(text.value).toBe(record.text);
});

test("the labeled connection tool creates a personal edge without requiring a precision handle drag", async () => {
  const initial: StudyCanvasRecord = {
    ...canvas,
    document: {
      ...canvas.document,
      nodes: ["a", "b"].map((id, index) => ({
        id,
        record_kind: "source",
        record_id: "m",
        x: 350 + index * 340,
        y: 100,
        width: 280,
        height: 250,
      })),
    },
  };
  const patches: Record<string, unknown>[] = [];
  openCanvas((body) => {
    patches.push(body);
    return Response.json({ ...initial, version: 8, document: body.document });
  }, initial);
  await userEvent.click(await screen.findByRole("button", { name: "连线" }));
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "连线起点卡片" }),
    "a",
  );
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "连线终点卡片" }),
    "b",
  );
  await userEvent.click(screen.getByRole("button", { name: "建立连线" }));
  await waitFor(() => expect(patches).toHaveLength(1), { timeout: 2500 });
  expect(patches[0].document).toMatchObject({
    edges: [
      { source: "a", target: "b", relation: "association", label: "关联" },
    ],
  });
});
