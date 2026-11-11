import { useEffect, useMemo, useRef, useState } from "react";
import { skipToken, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ReactFlow,
  Background,
  Controls,
  ControlButton,
  MarkerType,
  Position,
} from "@xyflow/react";
import type { Edge, Node, ReactFlowInstance, Viewport } from "@xyflow/react";
import {
  UserRound,
  Building2,
  MapPin,
  FileText,
  CalendarDays,
  BookOpen,
  Circle,
  LocateFixed,
  type LucideIcon,
} from "lucide-react";
import "@xyflow/react/dist/style.css";
import { Button } from "@/components/ui/button";
import {
  research,
  type ResearchGraphData,
  type ResearchNetworkData,
  type ResearchNetworkNode,
  type ResearchNetworkEdge,
} from "./api";
import Evidence from "./ResearchEvidence";
import { relationLabel } from "./relation-labels";
import { entityLabels } from "./labels";
import { eventNatureLabels } from "./EventNature";
import {
  layoutNetwork,
  NETWORK_NODE_WIDTH,
  NETWORK_NODE_HEIGHT,
  type NetworkPosition,
} from "./network-layout";

type GraphNode = ResearchGraphData["nodes"][number] | ResearchNetworkNode;
type GraphEdge = ResearchGraphData["edges"][number] | ResearchNetworkEdge;
type NetworkViewState = {
  positions: Array<[string, NetworkPosition]>;
  viewport?: Viewport;
};
const directedPredicates = new Set([
  "reply_to",
  "reprint_of",
  "support",
  "counter",
  "limit",
  "mentioned_in",
]);
const nodeIcons: Record<string, LucideIcon> = {
  person: UserRound,
  organization: Building2,
  place: MapPin,
  document: FileText,
  event: CalendarDays,
  card: BookOpen,
};
function isDirected(edge: GraphEdge) {
  if (typeof edge.directed === "boolean") return edge.directed;
  return directedPredicates.has(edge.predicate || edge.kind);
}
const relationStatusLabels = new Map<string, string>([
  ["planned", eventNatureLabels.planned],
  ["negated", eventNatureLabels.negated],
  ["conditional", eventNatureLabels.conditional],
]);
function relationText(edge: GraphEdge) {
  const label = relationLabel(edge.predicate, edge.kind);
  const status = relationStatusLabels.get(edge.event_status);
  return status ? `${label}（${status}）` : label;
}
export type ResearchGraphProps = {
  entityId: string;
  onEntity: (id: string) => void;
  onCard: (id: string) => void;
  data?: ResearchGraphData | ResearchNetworkData;
  viewKey?: string;
  selectedNodeId?: string;
  selectedEdgeId?: string;
  onSelectNode?: (node: GraphNode) => void;
  onSelectEdge?: (edge: GraphEdge) => void;
  hops?: number;
  onHopsChange?: (hops: 1 | 2) => void;
  loading?: boolean;
  error?: string;
  onRetry?: () => void;
};
export default function ResearchGraph({
  entityId,
  onEntity,
  onCard,
  data: suppliedData,
  viewKey,
  selectedNodeId,
  selectedEdgeId,
  onSelectNode,
  onSelectEdge,
  hops: suppliedHops,
  onHopsChange,
  loading,
  error,
  onRetry,
}: ResearchGraphProps) {
  const [localHops, setHops] = useState(1),
    [localSelected, setSelected] = useState<string>();
  const controlled = !!(onSelectNode || onSelectEdge || suppliedData);
  const hops = suppliedHops ?? localHops;
  const selected = controlled ? selectedEdgeId : localSelected;
  const graph = useQuery({
    queryKey: ["research", "graph", entityId, hops],
    queryFn: ({ signal }) => research.graph(entityId, hops, signal),
    staleTime: 30000,
    retry: false,
    enabled: !controlled,
  });
  const data = controlled ? suppliedData : graph.data;
  const cache = useQueryClient();
  const viewCacheKey = useMemo(
    () => ["research", "network-view", viewKey ?? null] as const,
    [viewKey],
  );
  useQuery<NetworkViewState>({
    queryKey: viewCacheKey,
    queryFn: skipToken,
    enabled: false,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
  });
  const initialView = useMemo(
    () =>
      viewKey ? cache.getQueryData<NetworkViewState>(viewCacheKey) : undefined,
    [cache, viewKey, viewCacheKey],
  );
  const viewport = useRef<ReactFlowInstance | null>(null);
  const retainedLayout = useRef<{
    centerId: string;
    viewKey?: string;
    positions: Map<string, NetworkPosition>;
  }>({
    centerId: entityId,
    viewKey,
    positions: new Map(initialView?.positions),
  });
  const positions = useMemo(() => {
    const previous =
      retainedLayout.current.centerId === entityId &&
      retainedLayout.current.viewKey === viewKey
        ? retainedLayout.current.positions
        : new Map(initialView?.positions);
    if (!data) return previous ?? new Map<string, NetworkPosition>();
    const positions = layoutNetwork(
      data?.nodes ?? [],
      data?.edges ?? [],
      entityId,
      previous,
    );
    retainedLayout.current = { centerId: entityId, viewKey, positions };
    return positions;
  }, [data?.nodes, data?.edges, entityId, viewKey, initialView]);
  useEffect(() => {
    if (!viewKey || !data) return;
    cache.setQueryData<NetworkViewState>(viewCacheKey, (current) => ({
      ...current,
      positions: [...positions],
    }));
  }, [cache, viewCacheKey, viewKey, data, positions]);
  const nodes = useMemo<Node[]>(
    () =>
      [...(data?.nodes ?? [])]
        .filter((item) => positions.has(item.id))
        .map((item) => {
          const Icon = nodeIcons[item.kind] || Circle;
          const kind =
            entityLabels[item.kind] ||
            (item.kind === "card" ? "出处卡" : "实体");
          return {
            id: item.id,
            data: {
              label: (
                <span className="research-network-node" data-kind={item.kind}>
                  <span className="research-network-node-kind">
                    <Icon size={15} aria-hidden="true" />
                    {kind}
                    {item.id === entityId && " · 中心"}
                  </span>
                  <span className="research-network-node-name">
                    {item.name}
                  </span>
                </span>
              ),
            },
            ariaLabel: `${kind} · ${item.name}`,
            selected: controlled && selectedNodeId === item.id,
            position: positions.get(item.id)!,
            type: "default",
            sourcePosition: Position.Right,
            targetPosition: Position.Left,
            style: {
              width: NETWORK_NODE_WIDTH,
              minHeight: NETWORK_NODE_HEIGHT,
              borderColor:
                selectedNodeId === item.id || item.id === entityId
                  ? "var(--primary)"
                  : "var(--border)",
              borderWidth: selectedNodeId === item.id ? 2 : 1,
              background: "var(--card)",
              color: "var(--foreground)",
              fontFamily: "var(--font-ui)",
              fontSize: 13,
            },
          };
        }),
    [data?.nodes, entityId, positions, controlled, selectedNodeId],
  );
  const edges = useMemo<Edge[]>(
    () =>
      data?.edges
        .filter(
          (item) =>
            positions.has(item.source_id) && positions.has(item.target_id),
        )
        .map((item) => ({
          id: item.id,
          source: item.source_id,
          target: item.target_id,
          label: relationText(item),
          selected: selected === item.id,
          type: "smoothstep",
          markerEnd: isDirected(item)
            ? {
                type: MarkerType.ArrowClosed,
                color:
                  selected === item.id
                    ? "var(--primary)"
                    : "var(--muted-foreground)",
              }
            : undefined,
          style: {
            stroke:
              selected === item.id
                ? "var(--primary)"
                : "var(--muted-foreground)",
            strokeWidth: selected === item.id ? 2.5 : 1.5,
          },
          labelStyle: { fill: "var(--foreground)", fontSize: 11 },
          labelBgStyle: { fill: "var(--card)" },
        })) ?? [],
    [data?.edges, selected, positions],
  );
  const edge = data?.edges.find((item) => item.id === selected);
  const centerId = positions.has(entityId)
    ? entityId
    : data?.entity_id || entityId;
  const centerFit = useMemo(
    () => ({
      nodes: [{ id: centerId }],
      padding: 0.22,
      minZoom: 0.7,
      maxZoom: 0.85,
    }),
    [centerId],
  );
  const nodeNames = useMemo(
    () => new Map(data?.nodes.map((node) => [node.id, node.name])),
    [data?.nodes],
  );
  function open(node: GraphNode) {
    if (onSelectNode) {
      onSelectNode(node);
      return;
    }
    if (node.kind === "card") {
      if (node.card_id) onCard(node.card_id);
    } else onEntity(node.canonical_id || node.id);
  }
  function selectEdge(item: GraphEdge) {
    if (onSelectEdge) onSelectEdge(item);
    else setSelected(item.id);
  }
  function changeHops(value: 1 | 2) {
    if (onHopsChange) onHopsChange(value);
    else setHops(value);
    setSelected(undefined);
  }
  function relationTitle(item: GraphEdge) {
    return `${nodeNames.get(item.source_id) || item.source_id} · ${relationText(item)} · ${nodeNames.get(item.target_id) || item.target_id}`;
  }
  return (
    <section className="research-graph-view" aria-label="证据关系视图">
      <header>
        <h3>关系与追查线索</h3>
        <div role="group" aria-label="关系范围">
          <Button
            variant={hops === 1 ? "default" : "outline"}
            aria-pressed={hops === 1}
            onClick={() => {
              changeHops(1);
            }}
          >
            直接关联
          </Button>
          <Button
            variant={hops === 2 ? "default" : "outline"}
            aria-pressed={hops === 2}
            onClick={() => {
              changeHops(2);
            }}
          >
            再追查一层
          </Button>
        </div>
      </header>
      <p className="research-provenance">
        初始聚焦中心；拖动画布查看周边，或用“查看全图”显示全部节点。单击实体或关系核对来源。
      </p>
      {(controlled ? loading : graph.isPending) && (
        <p role="status">正在读取关系与证据…</p>
      )}
      {(controlled ? error : graph.isError) && (
        <p role="alert">
          {error || graph.error?.message}
          <Button onClick={() => (onRetry ? onRetry() : void graph.refetch())}>
            重新读取关系
          </Button>
        </p>
      )}
      {data && (
        <>
          {nodes.length > 0 && (
            <div className="research-graph-canvas">
              <ReactFlow
                key={viewKey || entityId}
                nodes={nodes}
                edges={edges}
                fitView={!initialView?.viewport}
                defaultViewport={initialView?.viewport}
                fitViewOptions={centerFit}
                onMoveEnd={(_, nextViewport) => {
                  if (viewKey)
                    cache.setQueryData<NetworkViewState>(
                      viewCacheKey,
                      (current) => ({
                        positions: current?.positions ?? [...positions],
                        viewport: nextViewport,
                      }),
                    );
                }}
                onInit={(instance) => {
                  viewport.current = instance;
                }}
                minZoom={0.02}
                maxZoom={2}
                ariaLabelConfig={{
                  "controls.ariaLabel": "关系图缩放控件",
                  "controls.zoomIn.ariaLabel": "放大关系图",
                  "controls.zoomOut.ariaLabel": "缩小关系图",
                  "controls.fitView.ariaLabel": "查看全图",
                  "controls.interactive.ariaLabel": "切换关系图交互",
                  "node.a11yDescription.default":
                    "按回车或空格选择实体，按 Esc 取消选择。",
                  "node.a11yDescription.keyboardDisabled":
                    "按回车或空格选择实体，按 Esc 取消选择。",
                  "edge.a11yDescription.default":
                    "按回车或空格选择关系，按 Esc 取消选择。",
                  "handle.ariaLabel": "关系连接点",
                }}
                nodesDraggable={false}
                nodesConnectable={false}
                edgesReconnectable={false}
                connectOnClick={false}
                deleteKeyCode={null}
                onNodeClick={(_, node) => {
                  const item = data.nodes.find((item) => item.id === node.id);
                  if (item) open(item);
                }}
                onEdgeClick={(_, edge) => {
                  const item = data.edges.find((item) => item.id === edge.id);
                  if (item) selectEdge(item);
                }}
              >
                <Background gap={22} color="var(--border)" />
                <Controls
                  showInteractive={false}
                  fitViewOptions={{ padding: 0.22, minZoom: 0.02, maxZoom: 1 }}
                >
                  <ControlButton
                    aria-label="回到当前实体"
                    title="回到当前实体"
                    onClick={() => void viewport.current?.fitView(centerFit)}
                  >
                    <LocateFixed size={16} aria-hidden="true" />
                  </ControlButton>
                </Controls>
              </ReactFlow>
            </div>
          )}
          {data.edges.length === 0 && <p>当前范围没有可用关系记录。</p>}
          {data.truncated && (
            <p className="workspace-status-note">
              本次已加载 {data.nodes.length} 个节点、{data.edges.length}{" "}
              条关系，仍有未加载的关联。可继续加载、缩小筛选范围或沿相关条目追查。
            </p>
          )}
          {data.nodes.length > nodes.length && (
            <p className="workspace-status-note">
              为便于阅读，图中显示 {nodes.length} 个节点，另有{" "}
              {data.nodes.length - nodes.length}{" "}
              个节点未在画布展示；下方列表保留全部已加载记录。
            </p>
          )}
          <div className="research-graph-list">
            <h4>关系中的实体与卡片</h4>
            {data.nodes.map((node) => (
              <Button
                variant="ghost"
                key={node.id}
                aria-pressed={
                  controlled ? selectedNodeId === node.id : undefined
                }
                onClick={() => open(node)}
                disabled={
                  (!controlled && node.id === entityId) ||
                  (node.kind === "card" && !node.card_id)
                }
              >
                {node.name}
              </Button>
            ))}
          </div>
          {!!data.edges.length && (
            <div className="research-graph-list">
              <h4>核对关系依据</h4>
              <ul className="research-graph-relations">
                {data.edges.map((item) => (
                  <li key={item.id}>
                    <Button
                      variant="outline"
                      aria-label={
                        controlled
                          ? relationTitle(item)
                          : `查看关系依据 · ${relationLabel(item.predicate, item.kind)}`
                      }
                      aria-pressed={selected === item.id}
                      onClick={() => selectEdge(item)}
                    >
                      {relationTitle(item)}
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {!controlled && edge && "evidence" in edge && (
            <section className="research-selected-relation">
              <h4>{relationLabel(edge.predicate, edge.kind)}</h4>
              <p>{edge.reason}</p>
              <Button variant="ghost" onClick={() => onCard(edge.card_id)}>
                查看出处卡片
              </Button>
              {edge.target_card_id && (
                <Button
                  variant="ghost"
                  onClick={() => onCard(edge.target_card_id!)}
                >
                  查看对方出处卡片
                </Button>
              )}
              <Evidence
                evidence={(edge.evidence ?? []).map((item) => ({
                  ...item,
                  pages: item.pages ?? [],
                  footnote_ids: item.footnote_ids ?? [],
                }))}
                bookId={edge.book_id || ""}
              />
            </section>
          )}
        </>
      )}
    </section>
  );
}
