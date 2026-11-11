// @vitest-environment happy-dom
import { useRef, useState } from "react";
import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Edge, ReactFlowInstance } from "@xyflow/react";
import CanvasFlow from "./canvas-flow";
import { canvasNodeGeometry, type StudyFlowNode } from "./canvas-nodes";
import { useCanvasDocument } from "./canvas-state";
import type { StudyCanvasRecord } from "./types";
import { readFileSync } from "node:fs";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

const canvas: StudyCanvasRecord = {
  id: "c",
  topic_id: "t",
  title: "画布",
  version: 1,
  archived: false,
  created_at: 1,
  updated_at: 1,
  document: {
    viewport: { x: 0, y: 0, z: 1 },
    edges: [],
    nodes: ["first", "second"].map((id, index) => ({
      id,
      record_kind: "source",
      record_id: id,
      x: index * 320,
      y: 0,
      width: 280,
      height: 200,
    })),
  },
};

function CanvasSelectionHarness({
  value = canvas,
}: {
  value?: StudyCanvasRecord;
}) {
  const state = useCanvasDocument(value, "selection-test");
  const [selectedNodes, setSelectedNodes] = useState<string[]>([]);
  const [selectedEdges, setSelectedEdges] = useState<string[]>([]);
  const flow = useRef<ReactFlowInstance<StudyFlowNode, Edge> | null>(null);
  const nodes: StudyFlowNode[] = state.document.nodes.map((node) => ({
    id: node.id,
    type: "study-card",
    position: { x: node.x, y: node.y },
    selected: selectedNodes.includes(node.id),
    ...canvasNodeGeometry(node),
    data: {
      title: node.id,
      kind: "史料",
      text: "原文预览",
      meta: "",
      recordKind: node.record_kind,
      noteKind:
        node.record_kind === "group"
          ? "group"
          : node.record_kind === "source"
            ? "source"
            : "note",
      sourceCurrent: true,
      collapsed: false,
      onOpen: (id) => setSelectedNodes([id]),
      onToggle: () => {},
      beginGesture: state.beginGesture,
      endGesture: state.endGesture,
    },
  }));
  return (
    <CanvasFlow
      state={state}
      nodes={nodes}
      selectedEdges={selectedEdges}
      readonly={false}
      flow={flow}
      onNodeSelection={setSelectedNodes}
      onEdgeSelection={setSelectedEdges}
      onNode={() => {}}
      onEdge={() => {}}
      onClear={() => setSelectedNodes([])}
      onConnect={() => {}}
    />
  );
}

test("real canvas interactions retain both selected cards with Shift and Control modifiers", async () => {
  const view = render(
    <QueryClientProvider client={new QueryClient()}>
      <CanvasSelectionHarness />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("heading", { name: "first" }));
  expect(
    view.container.querySelectorAll(".react-flow__node.selected"),
  ).toHaveLength(1);
  await user.keyboard("{Shift>}");
  await user.click(screen.getByRole("heading", { name: "second" }));
  await user.keyboard("{/Shift}");
  expect(
    view.container.querySelectorAll(".react-flow__node.selected"),
  ).toHaveLength(2);
  await user.keyboard("{Control>}");
  await user.click(screen.getByRole("heading", { name: "second" }));
  await user.keyboard("{/Control}");
  expect(
    view.container.querySelectorAll(".react-flow__node.selected"),
  ).toHaveLength(1);
  expect(
    view.container
      .querySelector(".react-flow__node.selected")
      ?.getAttribute("data-id"),
  ).toBe("first");
});

test("opening a card is a single-selection command even while a modifier is held", async () => {
  const view = render(
    <QueryClientProvider client={new QueryClient()}>
      <CanvasSelectionHarness />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("heading", { name: "first" }));
  await user.keyboard("{Control>}");
  await user.click(screen.getByRole("button", { name: "打开 first" }));
  await user.keyboard("{/Control}");
  expect(
    view.container.querySelectorAll(".react-flow__node.selected"),
  ).toHaveLength(1);
  expect(
    view.container
      .querySelector(".react-flow__node.selected")
      ?.getAttribute("data-id"),
  ).toBe("first");
});

test("the real minimap uses defined theme colors, outlined groups and a mask sized to its panel", async () => {
  const value: StudyCanvasRecord = {
    ...canvas,
    document: {
      ...canvas.document,
      nodes: [
        ...canvas.document.nodes,
        {
          id: "group",
          record_kind: "group",
          record_id: "",
          x: 700,
          y: 0,
          width: 600,
          height: 400,
        },
        {
          id: "note",
          record_kind: "record",
          record_id: "note",
          x: 800,
          y: 50,
          width: 280,
          height: 200,
        },
      ],
    },
  };
  const view = render(
    <QueryClientProvider client={new QueryClient()}>
      <CanvasSelectionHarness value={value} />
    </QueryClientProvider>,
  );
  await screen.findByRole("img", { name: "画布缩略导航" });
  const panel = view.container.querySelector<HTMLElement>(
    ".react-flow__minimap",
  )!;
  const nodes = [
    ...panel.querySelectorAll<SVGRectElement>(".react-flow__minimap-node"),
  ];
  const themeCSS = readFileSync("src/styles/theme.css", "utf8");
  const definitions = new Set(
    [...themeCSS.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)].map((match) => match[1]),
  );
  const colors = [
    panel.getAttribute("style") || "",
    ...nodes.map((node) => node.getAttribute("style") || ""),
  ].join(" ");
  for (const token of colors.matchAll(/var\((--[a-zA-Z0-9-]+)/g))
    expect(definitions.has(token[1]), `undefined theme token ${token[1]}`).toBe(
      true,
    );
  expect(nodes[2].style.fill).toBe("none");
  expect(nodes[2].style.stroke).toBeTruthy();
  expect(nodes[3].style.fill).not.toBe(nodes[0].style.fill);
  expect(
    panel.style.getPropertyValue("--xy-minimap-mask-background-color-props"),
  ).toBe("var(--background)");
  expect(panel.querySelector("svg")?.getAttribute("width")).toBe("145");
  expect(panel.querySelector("svg")?.getAttribute("height")).toBe("100");
});
