import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Panel,
  Handle,
  Position,
  MarkerType,
  useUpdateNodeInternals,
} from "@xyflow/react";
import type { Node, NodeProps, Edge, ReactFlowInstance } from "@xyflow/react";
import { layoutGraph } from "./agent-graph.ts";
import type {
  ExecutionNode,
  ExecutionEdge,
  Position as Point,
} from "./agent-graph.ts";
import "@xyflow/react/dist/style.css";

type RuntimeNode = Node<
  { record: ExecutionNode; content: ReactNode; ports: string[] },
  "runtime"
>;
function RuntimeNodeView({ id, data, selected }: NodeProps<RuntimeNode>) {
  const update = useUpdateNodeInternals();
  const portKey = data.ports.join("|");
  useEffect(() => {
    update(id);
  }, [id, portKey, update]);
  return (
    <>
      <Handle id="in" type="target" position={Position.Left} />
      <article
        className="execution-node"
        data-state={data.record.state}
        data-kind={data.record.kind}
        data-selected={selected}
      >
        {data.content}
      </article>
      {data.ports.map((port, index) => (
        <Handle
          key={port}
          id={port}
          type="source"
          position={Position.Right}
          style={{
            top: `${28 + ((index + 1) * 48) / (data.ports.length + 1)}%`,
          }}
        />
      ))}
    </>
  );
}
const nodeTypes = { runtime: RuntimeNodeView };
const colors: Record<string, string> = {
  delegates: "#386e9e",
  calls: "#70849b",
  returns: "#368872",
  produces: "#8871a0",
  waits_for: "#bd8033",
};
const relations: Record<string, string> = {
  delegates: "委派任务",
  calls: "调用",
  returns: "回传成果",
  produces: "保存成果",
  waits_for: "等待",
  depends_on: "依赖",
};
const aria = {
  "controls.ariaLabel": "画布控制",
  "controls.zoomIn.ariaLabel": "放大关系图",
  "controls.zoomOut.ariaLabel": "缩小关系图",
  "controls.fitView.ariaLabel": "查看全图",
  "minimap.ariaLabel": "关系图缩略导航",
  "node.a11yDescription.default":
    "按 Enter 选择节点，方向键移动节点；移动仅调整显示位置。",
};

export function ExecutionCanvas({
  nodes,
  edges,
  selected,
  revision,
  renderNode,
}: {
  nodes: ExecutionNode[];
  edges: ExecutionEdge[];
  selected: string | null;
  revision: number;
  renderNode: (node: ExecutionNode) => ReactNode;
}) {
  const positions = useRef(new Map<string, Point>()),
    lastRevision = useRef(revision);
  const [flow, setFlow] = useState<ReactFlowInstance<RuntimeNode> | null>(null),
    [zoom, setZoom] = useState(100),
    [dragRevision, setDragRevision] = useState(0);
  if (lastRevision.current !== revision) {
    positions.current = new Map();
    lastRevision.current = revision;
  }
  const coordinates = useMemo(() => {
    positions.current = layoutGraph(nodes, edges, positions.current);
    return positions.current;
  }, [nodes, edges, revision, dragRevision]);
  const flowNodes: RuntimeNode[] = nodes.map((record) => ({
    id: record.id,
    type: "runtime",
    dragHandle: ".execution-node-top",
    position: coordinates.get(record.id)!,
    width: 280,
    height: 184,
    selected: record.id === selected,
    data: {
      record,
      content: renderNode(record),
      ports: edges
        .filter((e) => e.source === record.id)
        .map((e) => "out:" + e.id),
    },
  }));
  const flowEdges: Edge[] = edges.map((edge) => {
    const connected =
      !selected || edge.source === selected || edge.target === selected;
    const running =
      nodes.find((n) => n.id === edge.target)?.state === "running";
    const color = colors[edge.relation] || "#70849b";
    return {
      ...edge,
      type: edge.relation === "returns" ? "default" : "smoothstep",
      sourceHandle: "out:" + edge.id,
      targetHandle: "in",
      label: relations[edge.relation] || edge.relation,
      animated: running && connected,
      selectable: false,
      style: {
        stroke: color,
        strokeWidth: connected ? 2 : 1.3,
        opacity: connected ? 1 : 0.35,
        strokeDasharray: edge.relation === "returns" ? "6 5" : undefined,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 18, height: 18 },
      labelStyle: { fill: color, fontSize: 11 },
      labelBgStyle: { fill: "#f8fafc", fillOpacity: 0.96 },
      labelBgPadding: [7, 4],
      labelBgBorderRadius: 5,
    };
  });
  const initialFocus = nodes.filter((n) => n.kind === "agent").slice(0, 12);
  const fitOptions = {
    padding: 0.25,
    maxZoom: 1,
    minZoom: 0.45,
    nodes: initialFocus.length ? initialFocus : nodes.slice(0, 8),
  };
  useEffect(() => {
    const position = selected ? positions.current.get(selected) : null;
    if (flow && position)
      void flow.setCenter(position.x + 140, position.y + 92, {
        zoom: flow.getZoom(),
        duration: 0,
      });
  }, [flow, selected]);
  useEffect(() => {
    if (flow && revision) void flow.fitView(fitOptions);
  }, [revision]);
  return (
    <div className="execution-viewport" aria-label="可拖拽缩放的任务执行画布">
      <ReactFlow<RuntimeNode>
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        onInit={setFlow}
        fitView
        fitViewOptions={fitOptions}
        minZoom={0.15}
        maxZoom={1.6}
        nodesConnectable={false}
        edgesReconnectable={false}
        deleteKeyCode={null}
        connectOnClick={false}
        selectNodesOnDrag={false}
        ariaLabelConfig={aria}
        onMove={(_, view) => setZoom(Math.round(view.zoom * 100))}
        onNodesChange={(changes) => {
          let moved = false;
          for (const change of changes)
            if (change.type === "position" && change.position) {
              positions.current.set(change.id, change.position);
              moved = true;
            }
          if (moved) setDragRevision((v) => v + 1);
        }}
        onNodeDragStop={(_, node) => {
          positions.current.set(node.id, node.position);
          setDragRevision((v) => v + 1);
        }}
        defaultEdgeOptions={{ type: "smoothstep" }}
      >
        <Background color="#d8e2ec" gap={22} size={1} />
        <Controls showInteractive={false} />
        <MiniMap<RuntimeNode>
          pannable
          zoomable
          nodeStrokeWidth={3}
          nodeColor={(node) =>
            node.data.record.state === "running"
              ? "#4885b0"
              : ["failed", "interrupted"].includes(node.data.record.state)
                ? "#cca460"
                : "#becbd8"
          }
        />
        <Panel position="top-left" className="execution-canvas-legend">
          <span>
            <i />
            委派
          </span>
          <span>
            <i />
            工具 / 服务
          </span>
          <span>
            <i />
            回传
          </span>
        </Panel>
        <Panel position="top-right" className="execution-canvas-zoom">
          {zoom}% · 拖动画布浏览
        </Panel>
      </ReactFlow>
      {!nodes.length && (
        <p className="execution-canvas-empty">当前没有可显示的执行节点。</p>
      )}
    </div>
  );
}
