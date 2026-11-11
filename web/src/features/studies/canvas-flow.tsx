import type { Dispatch, RefObject, SetStateAction } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  MarkerType,
  type Edge,
  type NodeChange,
  type ReactFlowInstance,
} from "@xyflow/react";
import { reparentDroppedNodes } from "./canvas-model";
import { studyNodeTypes, type StudyFlowNode } from "./canvas-nodes";
import type { useCanvasDocument } from "./canvas-state";

const multiSelectionKeys = ["Control", "Meta", "Shift"];
const miniMapFills: Record<string, string> = {
  source: "var(--accent)",
  argument: "var(--success-surface)",
  thought: "var(--warning-surface)",
  question: "var(--destructive-surface)",
  note: "var(--secondary)",
  clue: "var(--muted-surface)",
};
const miniMapStrokes: Record<string, string> = {
  argument: "var(--success)",
  thought: "var(--warning)",
  question: "var(--destructive)",
  note: "var(--muted-foreground)",
};
function miniMapNodeFill(node: StudyFlowNode) {
  return node.data.recordKind === "group"
    ? "none"
    : miniMapFills[node.data.noteKind] || "var(--muted-surface)";
}
function miniMapNodeStroke(node: StudyFlowNode) {
  return node.selected
    ? "var(--ring)"
    : miniMapStrokes[node.data.noteKind] || "var(--primary)";
}

/** Translate the canvas engine's transient events into neutral document changes. */
export default function CanvasFlow({
  state,
  nodes,
  selectedEdges,
  readonly,
  flow,
  onNodeSelection,
  onEdgeSelection,
  onNode,
  onEdge,
  onClear,
  onConnect,
}: {
  state: ReturnType<typeof useCanvasDocument>;
  nodes: StudyFlowNode[];
  selectedEdges: string[];
  readonly: boolean;
  flow: RefObject<ReactFlowInstance<StudyFlowNode, Edge> | null>;
  onNodeSelection: Dispatch<SetStateAction<string[]>>;
  onEdgeSelection: Dispatch<SetStateAction<string[]>>;
  onNode: (id: string, edit: boolean) => void;
  onEdge: (id: string) => void;
  onClear: () => void;
  onConnect: (source: string, target: string) => void;
}) {
  const document = state.document;
  const visibleIds = new Set(
    nodes.filter((node) => !node.hidden).map((node) => node.id),
  );
  const edgeSelection = new Set(selectedEdges);
  const edges: Edge[] = document.edges.map((item) => ({
    ...item,
    selected: edgeSelection.has(item.id),
    hidden: !visibleIds.has(item.source) || !visibleIds.has(item.target),
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed },
    style: {
      stroke: "var(--foreground)",
      strokeWidth: edgeSelection.has(item.id) ? 2.5 : 1.25,
    },
    labelStyle: { fill: "var(--foreground)" },
    labelBgStyle: { fill: "var(--background)" },
  }));

  function changeNodes(changes: NodeChange<StudyFlowNode>[]) {
    const selectionChanges = changes.filter(
      (change) => change.type === "select",
    );
    if (selectionChanges.length)
      onNodeSelection((previous) => {
        const selection = new Set(previous);
        for (const change of selectionChanges) {
          if (change.selected) selection.add(change.id);
          else selection.delete(change.id);
        }
        return [...selection];
      });
    const geometry = new Map<string, NodeChange<StudyFlowNode>[]>();
    for (const change of changes) {
      if (change.type === "position" || change.type === "dimensions") {
        const nodeChanges = geometry.get(change.id) || [];
        nodeChanges.push(change);
        geometry.set(change.id, nodeChanges);
      }
    }
    if (readonly || !geometry.size) return;
    const next = document.nodes.map((item) => {
      let value = item;
      for (const change of geometry.get(item.id) || []) {
        if (change.type === "position" && change.position)
          value = { ...value, x: change.position.x, y: change.position.y };
        if (change.type === "dimensions" && change.dimensions)
          value = {
            ...value,
            width: change.dimensions.width,
            height: change.dimensions.height,
          };
      }
      return value;
    });
    state.apply({ ...document, nodes: next }, false);
  }

  return (
    <ReactFlow<StudyFlowNode, Edge>
      nodes={nodes}
      edges={edges}
      nodeTypes={studyNodeTypes}
      onInit={(instance) => {
        flow.current = instance;
      }}
      defaultViewport={{
        x: document.viewport.x,
        y: document.viewport.y,
        zoom: document.viewport.z,
      }}
      onMoveEnd={(_, view) => {
        if (!readonly)
          state.apply(
            {
              ...document,
              viewport: { x: view.x, y: view.y, z: view.zoom },
            },
            false,
          );
      }}
      onNodesChange={changeNodes}
      onEdgesChange={(changes) => {
        const selectionChanges = changes.filter(
          (change) => change.type === "select",
        );
        if (selectionChanges.length)
          onEdgeSelection((previous) => {
            const selection = new Set(previous);
            for (const change of selectionChanges) {
              if (change.selected) selection.add(change.id);
              else selection.delete(change.id);
            }
            return [...selection];
          });
      }}
      onNodeClick={(_, item) => onNode(item.id, false)}
      onNodeDoubleClick={(_, item) => onNode(item.id, true)}
      onEdgeClick={(_, item) => onEdge(item.id)}
      onPaneClick={onClear}
      onNodeDragStart={state.beginGesture}
      onNodeDragStop={(_, item, moved) => {
        const final = new Map(
          [...moved, item].map((node) => [node.id, node.position]),
        );
        const next = {
          ...document,
          nodes: document.nodes.map((node) => {
            const position = final.get(node.id);
            return position ? { ...node, ...position } : node;
          }),
        };
        state.endGesture(reparentDroppedNodes(next, [...final.keys()]));
      }}
      onConnect={(connection) =>
        onConnect(connection.source, connection.target)
      }
      nodesDraggable={!readonly}
      nodesConnectable={!readonly}
      edgesReconnectable={false}
      deleteKeyCode={null}
      minZoom={0.15}
      maxZoom={2}
      panOnScroll
      selectionOnDrag
      multiSelectionKeyCode={multiSelectionKeys}
      onlyRenderVisibleElements={nodes.length > 150}
      ariaLabelConfig={{
        "controls.ariaLabel": "画布视图控件",
        "controls.zoomIn.ariaLabel": "放大画布",
        "controls.zoomOut.ariaLabel": "缩小画布",
        "controls.fitView.ariaLabel": "显示全部卡片",
        "controls.interactive.ariaLabel": "切换画布交互",
      }}
    >
      <Background gap={24} color="var(--input)" className="study-canvas-grid" />
      <Controls showInteractive={false} />
      <MiniMap<StudyFlowNode>
        pannable
        zoomable
        ariaLabel="画布缩略导航"
        className="study-canvas-minimap"
        style={{ width: 145, height: 100 }}
        bgColor="var(--card)"
        nodeColor={miniMapNodeFill}
        nodeStrokeColor={miniMapNodeStroke}
        nodeStrokeWidth={4}
        maskColor="var(--background)"
        maskStrokeColor="var(--primary)"
        maskStrokeWidth={1}
      />
    </ReactFlow>
  );
}
