// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ResearchGraph from "./ResearchGraph";
import type { ResearchGraphData, ResearchNetworkData } from "./api";
import type { ControlProps, Edge, Node, ReactFlowProps } from "@xyflow/react";
import type { ButtonHTMLAttributes } from "react";
const { flow, controls } = vi.hoisted(() => ({
  flow: vi.fn(),
  controls: vi.fn(),
}));
vi.mock("@xyflow/react", () => ({
  ReactFlow: (props: ReactFlowProps) => {
    flow(props);
    return props.children;
  },
  Background: () => null,
  Controls: (props: ControlProps) => {
    controls(props);
    return props.children;
  },
  ControlButton: (props: ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props} />
  ),
  MarkerType: { ArrowClosed: "arrowclosed" },
  Position: { Left: "left", Right: "right" },
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("records outside the canvas budget remain selectable in the complete source list", async () => {
  const selectNode = vi.fn(),
    selectEdge = vi.fn();
  const events = Array.from({ length: 81 }, (_, index) => ({
    id: `event-${String(index).padStart(2, "0")}`,
    name: `事项${index}`,
    kind: "event",
  }));
  const data: ResearchGraphData = {
    entity_id: "person",
    hops: 2,
    limit: 200,
    truncated: false,
    nodes: [{ id: "person", name: "中心人物", kind: "person" }, ...events],
    edges: events.map((event) => ({
      id: `edge-${event.id}`,
      source_id: "person",
      target_id: event.id,
      kind: "participant",
      predicate: "发函者",
      role: "发函者",
      reason: "原文记载",
      card_id: "card",
      card_version: 1,
      book_id: "book",
      run_id: "run",
      source_current: true,
      target_current: true,
      needs_update: false,
      event_status: "unknown",
      status_note: "",
    })),
  };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ResearchGraph
        entityId="person"
        data={data}
        onEntity={vi.fn()}
        onCard={vi.fn()}
        onSelectNode={selectNode}
        onSelectEdge={selectEdge}
      />
    </QueryClientProvider>,
  );
  const canvas = flow.mock.lastCall![0] as ReactFlowProps;
  expect(canvas.nodes).toHaveLength(80);
  expect(canvas.nodes?.some((node) => node.id === "event-80")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "事项80" }));
  await userEvent.click(
    screen.getByRole("button", {
      name: "中心人物 · 发函者 · 事项80",
    }),
  );
  expect(selectNode).toHaveBeenCalledWith(events[80]);
  expect(selectEdge).toHaveBeenCalledWith(data.edges[80]);
});

test("initial graph view keeps the center readable and reserves all-node fitting for the localized control", () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ResearchGraph
        entityId="person"
        onEntity={vi.fn()}
        onCard={vi.fn()}
        onSelectNode={vi.fn()}
        data={{
          entity_id: "person",
          hops: 1,
          limit: 30,
          truncated: false,
          nodes: [
            { id: "person", name: "中心人物", kind: "person" },
            ...Array.from({ length: 13 }, (_, index) => ({
              id: `event-${index}`,
              name: `事项${index}`,
              kind: "event",
            })),
          ],
          edges: [],
        }}
      />
    </QueryClientProvider>,
  );
  const props = flow.mock.lastCall![0] as ReactFlowProps;
  expect(props.fitViewOptions?.nodes).toEqual([{ id: "person" }]);
  expect(props.fitViewOptions?.minZoom).toBeGreaterThanOrEqual(0.7);
  expect(props.fitViewOptions?.maxZoom).toBeGreaterThanOrEqual(0.7);
  expect(props.ariaLabelConfig?.["controls.fitView.ariaLabel"]).toBe(
    "查看全图",
  );
  expect(props.ariaLabelConfig?.["controls.zoomIn.ariaLabel"]).toBe(
    "放大关系图",
  );
  const allNodes = (controls.mock.lastCall![0] as ControlProps).fitViewOptions;
  expect(allNodes?.nodes).toBeUndefined();
  expect(allNodes?.minZoom).toBeLessThan(0.7);
});

test("the current-entity control restores the readable center after panning or viewing all nodes", async () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ResearchGraph
        entityId="person"
        onEntity={vi.fn()}
        onCard={vi.fn()}
        onSelectNode={vi.fn()}
        data={{
          entity_id: "person",
          hops: 1,
          limit: 30,
          truncated: false,
          nodes: [{ id: "person", name: "中心人物", kind: "person" }],
          edges: [],
        }}
      />
    </QueryClientProvider>,
  );
  const fitView = vi.fn(async () => true);
  flow.mock.lastCall![0].onInit?.({ fitView });
  await userEvent.click(screen.getByRole("button", { name: "回到当前实体" }));
  expect(fitView).toHaveBeenCalledWith(
    expect.objectContaining({
      nodes: [{ id: "person" }],
      minZoom: 0.7,
      maxZoom: 0.85,
    }),
  );
});

test("returning from original evidence restores the same scoped camera and expanded node positions", () => {
  const client = new QueryClient();
  const relation: ResearchGraphData["edges"][number] = {
    id: "z-edge",
    source_id: "person",
    target_id: "z",
    kind: "association",
    predicate: "关联",
    reason: "",
    role: "",
    card_id: "card",
    card_version: 1,
    book_id: "book",
    run_id: "run",
    source_current: true,
    target_current: true,
    needs_update: false,
    event_status: "unknown",
    status_note: "",
  };
  const initial: ResearchGraphData = {
    entity_id: "person",
    hops: 1,
    limit: 30,
    truncated: false,
    nodes: [
      { id: "person", name: "中心人物", kind: "person" },
      { id: "z", name: "先读事项", kind: "event" },
    ],
    edges: [relation],
  };
  const expanded: ResearchGraphData = {
    ...initial,
    hops: 2,
    nodes: [...initial.nodes, { id: "a", name: "新展开事项", kind: "event" }],
    edges: [...initial.edges, { ...relation, id: "a-edge", target_id: "a" }],
  };
  const view = (data: ResearchGraphData, viewKey = "person:book") => (
    <QueryClientProvider client={client}>
      <ResearchGraph
        entityId="person"
        viewKey={viewKey}
        data={data}
        onSelectNode={vi.fn()}
        onEntity={vi.fn()}
        onCard={vi.fn()}
      />
    </QueryClientProvider>
  );
  const first = render(view(initial));
  first.rerender(view(expanded));
  const positions = new Map(
    (flow.mock.lastCall![0].nodes as Node[]).map((node) => [
      node.id,
      node.position,
    ]),
  );
  const camera = { x: -320, y: 76, zoom: 0.72 };
  flow.mock.lastCall![0].onMoveEnd?.(null, camera);
  first.unmount();
  const returned = render(view(expanded));
  const restored = flow.mock.lastCall![0] as ReactFlowProps;
  expect(restored.fitView).toBe(false);
  expect(restored.defaultViewport).toEqual(camera);
  expect(
    client.getQueryData(["research", "network-view", "person:book"]),
  ).toEqual({ positions: expect.any(Array), viewport: camera });
  expect(
    new Map(restored.nodes?.map((node) => [node.id, node.position])),
  ).toEqual(positions);
  expect(
    client
      .getQueryCache()
      .find({ queryKey: ["research", "network-view", "person:book"] })?.gcTime,
  ).toBe(30 * 60 * 1000);
  returned.unmount();
  render(view(expanded, "person:another-book"));
  const otherScope = flow.mock.lastCall![0] as ReactFlowProps;
  expect(otherScope.fitView).toBe(true);
  expect(otherScope.defaultViewport).toBeUndefined();
});

test("graph keeps event participation as its own edge and only draws arrows for directed relations", () => {
  const relation: ResearchGraphData["edges"][number] = {
    id: "participant",
    source_id: "person",
    target_id: "event",
    kind: "participant",
    predicate: "参与者",
    reason: "",
    role: "",
    card_id: "card",
    card_version: 1,
    book_id: "book",
    run_id: "run",
    source_current: true,
    target_current: true,
    needs_update: false,
    event_status: "unknown",
    status_note: "",
  };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ResearchGraph
        entityId="person"
        onEntity={vi.fn()}
        onCard={vi.fn()}
        onSelectNode={vi.fn()}
        data={{
          entity_id: "person",
          hops: 1,
          limit: 50,
          truncated: false,
          nodes: [
            { id: "person", name: "张某", kind: "person" },
            { id: "event", name: "拟拨经费", kind: "event" },
            { id: "document", name: "回复", kind: "document" },
          ],
          edges: [
            relation,
            {
              ...relation,
              id: "reply",
              source_id: "document",
              target_id: "event",
              kind: "reply_to",
              predicate: "reply_to",
            },
          ],
        }}
      />
    </QueryClientProvider>,
  );
  const props = flow.mock.lastCall![0] as { nodes: Node[]; edges: Edge[] };
  expect(props.edges).toHaveLength(2);
  expect(props.edges[0]).toMatchObject({ source: "person", target: "event" });
  expect(props.edges[0].markerEnd).toBeUndefined();
  expect(props.edges[1].markerEnd).toBeDefined();
  expect(props.nodes.every((node) => node.type === "default")).toBe(true);
});

test("relation labels retain planned, negated and conditional source status without certifying reported events", () => {
  const statuses = ["planned", "negated", "conditional", "reported", "unknown"];
  const edges: ResearchGraphData["edges"] = statuses.map((event_status) => ({
    id: event_status,
    source_id: "person",
    target_id: "event",
    kind: "participant",
    predicate: "参与者",
    reason: "",
    role: "",
    card_id: "card",
    card_version: 1,
    book_id: "book",
    run_id: "run",
    source_current: true,
    target_current: true,
    needs_update: false,
    event_status,
    status_note: "",
  }));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ResearchGraph
        entityId="person"
        onEntity={vi.fn()}
        onCard={vi.fn()}
        onSelectEdge={vi.fn()}
        data={{
          entity_id: "person",
          hops: 1,
          limit: 30,
          truncated: false,
          nodes: [
            { id: "person", name: "张某", kind: "person" },
            { id: "event", name: "经费方案", kind: "event" },
          ],
          edges,
        }}
      />
    </QueryClientProvider>,
  );
  expect(
    screen.getByRole("button", {
      name: "张某 · 参与者（计划或拟议事项） · 经费方案",
    }),
  ).toBeTruthy();
  expect(
    screen.getByRole("button", {
      name: "张某 · 参与者（原文否定发生） · 经费方案",
    }),
  ).toBeTruthy();
  expect(
    screen.getByRole("button", {
      name: "张某 · 参与者（附带条件的事项） · 经费方案",
    }),
  ).toBeTruthy();
  const props = flow.mock.lastCall![0] as ReactFlowProps;
  expect(props.edges?.find((edge) => edge.id === "planned")?.label).toBe(
    "参与者（计划或拟议事项）",
  );
  expect(props.edges?.find((edge) => edge.id === "reported")?.label).toBe(
    "参与者",
  );
});

test("expanding a controlled graph preserves the positions of already visible nodes", () => {
  const client = new QueryClient();
  const metadata = {
    version: 1,
    book_ids: ["book"],
    card_ids: ["card"],
    book_count: 1,
    card_count: 1,
    metadata_truncated: false,
    source_current: true,
    needs_update: false,
  };
  const base: ResearchNetworkData = {
    entity_id: "person",
    hops: 1,
    limit: 50,
    truncated: false,
    usage: "evidence-linked research leads",
    nodes: [
      { ...metadata, id: "person", name: "中心", kind: "person" },
      { ...metadata, id: "z", name: "乙", kind: "event" },
    ],
    edges: [
      {
        id: "edge",
        source_id: "person",
        target_id: "z",
        kind: "participant",
        predicate: "参与",
        card_id: "card",
        card_version: 1,
        book_id: "book",
        evidence_count: 1,
        date_basis: "event",
        event_status: "unknown",
        status_note: "",
        source_current: true,
        needs_update: false,
      },
    ],
  };
  const view = (data: typeof base) => (
    <QueryClientProvider client={client}>
      <ResearchGraph
        entityId="person"
        onEntity={vi.fn()}
        onCard={vi.fn()}
        onSelectNode={vi.fn()}
        data={data}
      />
    </QueryClientProvider>
  );
  const rendered = render(view(base));
  const before = new Map(
    (flow.mock.lastCall![0].nodes as Node[]).map((node) => [
      node.id,
      node.position,
    ]),
  );
  rendered.rerender(
    view({
      ...base,
      hops: 2,
      nodes: [
        ...base.nodes,
        { ...metadata, id: "a", name: "甲", kind: "place" },
      ],
      edges: [
        ...base.edges,
        { ...base.edges[0], id: "expanded", source_id: "z", target_id: "a" },
      ],
    }),
  );
  const after = new Map(
    (flow.mock.lastCall![0].nodes as Node[]).map((node) => [
      node.id,
      node.position,
    ]),
  );
  expect(after.get("person")).toEqual(before.get("person"));
  expect(after.get("z")).toEqual(before.get("z"));
});

test("controlled node selection keeps the workspace center and does not navigate or fetch", async () => {
  const select = vi.fn(),
    entity = vi.fn(),
    card = vi.fn(),
    request = vi.fn();
  vi.stubGlobal("fetch", request);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ResearchGraph
        entityId="person"
        onEntity={entity}
        onCard={card}
        data={{
          entity_id: "person",
          hops: 1,
          limit: 50,
          truncated: false,
          nodes: [
            { id: "person", kind: "person", name: "张某" },
            { id: "event", kind: "event", name: "拟拨经费" },
          ],
          edges: [],
        }}
        selectedNodeId="event"
        onSelectNode={select}
      />
    </QueryClientProvider>,
  );
  const event = screen.getByRole("button", { name: "拟拨经费" });
  expect(event.getAttribute("aria-pressed")).toBe("true");
  await userEvent.click(event);
  expect(select).toHaveBeenCalledWith(expect.objectContaining({ id: "event" }));
  expect(entity).not.toHaveBeenCalled();
  expect(card).not.toHaveBeenCalled();
  expect(request).not.toHaveBeenCalled();
});

test("controlled relation list names both endpoints and selects the source-bound relation without loading evidence", async () => {
  const select = vi.fn();
  const edge: ResearchGraphData["edges"][number] = {
    id: "edge",
    source_id: "person",
    target_id: "event",
    kind: "participant",
    predicate: "发函者",
    reason: "拟议事项",
    role: "发函者",
    card_id: "card",
    card_version: 2,
    book_id: "book",
    run_id: "run",
    source_current: true,
    target_current: true,
    needs_update: false,
    event_status: "unknown",
    status_note: "",
    evidence: [
      {
        id: "e",
        article_id: "article",
        start: 0,
        end: 12,
        text_sha256: "hash",
        quote: "不应在图中读取的完整证据",
        pages: [],
        footnote_ids: [],
      },
    ],
  };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ResearchGraph
        entityId="person"
        onEntity={vi.fn()}
        onCard={vi.fn()}
        data={{
          entity_id: "person",
          hops: 1,
          limit: 50,
          truncated: false,
          nodes: [
            { id: "person", name: "张某", kind: "person" },
            { id: "event", name: "拟拨经费", kind: "event" },
          ],
          edges: [edge],
        }}
        selectedEdgeId="edge"
        onSelectEdge={select}
      />
    </QueryClientProvider>,
  );
  const relation = screen.getByRole("button", {
    name: "张某 · 发函者 · 拟拨经费",
  });
  expect(relation.getAttribute("aria-pressed")).toBe("true");
  await userEvent.click(relation);
  expect(select).toHaveBeenCalledWith(edge);
  expect(screen.queryByText("不应在图中读取的完整证据")).toBeNull();
});

test("a document entity opens its identity rather than its source card", async () => {
  const entity = vi.fn(),
    card = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        entity_id: "person",
        hops: 1,
        limit: 50,
        truncated: false,
        nodes: [
          {
            id: "doc",
            canonical_id: "canonical-document",
            kind: "document",
            name: "某文献",
            card_id: "source-card",
          },
          {
            id: "card-node",
            kind: "card",
            name: "出处卡",
            card_id: "source-card",
          },
        ],
        edges: [],
      }),
    ),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchGraph entityId="person" onEntity={entity} onCard={card} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(await screen.findByRole("button", { name: "某文献" }));
  expect(entity).toHaveBeenCalledWith("canonical-document");
  expect(card).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "出处卡" }));
  expect(card).toHaveBeenCalledWith("source-card");
});

test("system graph edges have Chinese labels in their action and selected evidence heading", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        entity_id: "person",
        hops: 1,
        limit: 50,
        truncated: false,
        nodes: [
          { id: "person", kind: "person", name: "陈毅" },
          { id: "document", kind: "card", name: "文献", card_id: "card" },
        ],
        edges: [
          {
            id: "edge",
            source_id: "person",
            target_id: "document",
            kind: "mentioned_in",
            predicate: "mentioned_in",
            reason: "来源记载",
            card_id: "card",
            evidence: [],
          },
        ],
      }),
    ),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchGraph
          entityId="person"
          onEntity={() => {}}
          onCard={() => {}}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "查看关系依据 · 见于材料" }),
  );
  expect(screen.getByRole("heading", { name: "见于材料" })).toBeTruthy();
  expect(screen.queryByText("mentioned_in")).toBeNull();
});
test("a relationship view opens the other endpoint and the retained evidence behind an edge", async () => {
  const entity = vi.fn(),
    card = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        entity_id: "person",
        hops: 1,
        limit: 50,
        truncated: false,
        nodes: [
          { id: "person", kind: "person", name: "张某" },
          { id: "event", kind: "event", name: "拨款申请事件" },
        ],
        edges: [
          {
            id: "edge",
            source_id: "person",
            target_id: "event",
            predicate: "发函者",
            kind: "participant",
            reason: "仅记载提出申请",
            card_id: "card",
            evidence: [
              {
                id: "e",
                quote: "张某函请拨款。",
                pages: [5],
                footnote_ids: [],
              },
            ],
          },
        ],
      }),
    ),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchGraph entityId="person" onEntity={entity} onCard={card} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "拨款申请事件" }),
  );
  expect(entity).toHaveBeenCalledWith("event");
  await userEvent.click(
    screen.getByRole("button", { name: "查看关系依据 · 发函者" }),
  );
  await screen.findByText("张某函请拨款。");
  await userEvent.click(screen.getByRole("button", { name: "查看出处卡片" }));
  expect(card).toHaveBeenCalledWith("card");
});
