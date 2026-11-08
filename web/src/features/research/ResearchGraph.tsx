import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ReactFlow, Background, Controls } from "@xyflow/react";
import type { Edge, Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Button } from "@/components/ui/button";
import { research, type ResearchGraphData } from "./api";
import Evidence from "./ResearchEvidence";
import QuestionPicker from "./QuestionPicker";
import { relationLabel } from "./relation-labels";

type GraphNode = ResearchGraphData["nodes"][number];
export default function ResearchGraph({
  entityId,
  onEntity,
  onCard,
}: {
  entityId: string;
  onEntity: (id: string) => void;
  onCard: (id: string) => void;
}) {
  const [hops, setHops] = useState(1),
    [selected, setSelected] = useState<string>();
  const graph = useQuery({
    queryKey: ["research", "graph", entityId, hops],
    queryFn: ({ signal }) => research.graph(entityId, hops, signal),
    staleTime: 30000,
    retry: false,
  });
  const nodes = useMemo<Node[]>(
    () =>
      [...(graph.data?.nodes ?? [])]
        .sort((a, b) =>
          a.id === entityId
            ? -1
            : b.id === entityId
              ? 1
              : a.name.localeCompare(b.name),
        )
        .map((item, index) => ({
          id: item.id,
          data: { label: item.name },
          position: {
            x: index === 0 ? 0 : 300 + Math.floor((index - 1) / 6) * 260,
            y: index === 0 ? 140 : ((index - 1) % 6) * 100,
          },
          type: index === 0 ? "input" : "default",
          style: {
            width: 190,
            borderColor:
              item.id === entityId ? "var(--primary)" : "var(--border)",
            background: "var(--card)",
            color: "var(--foreground)",
            fontFamily: "var(--font-ui)",
            fontSize: 13,
          },
        })),
    [graph.data?.nodes, entityId],
  );
  const edges = useMemo<Edge[]>(
    () =>
      graph.data?.edges.map((item) => ({
        id: item.id,
        source: item.source_id,
        target: item.target_id,
        label: relationLabel(item.predicate, item.kind),
        type: "smoothstep",
        style: {
          stroke:
            selected === item.id ? "var(--primary)" : "var(--muted-foreground)",
          strokeWidth: selected === item.id ? 2.5 : 1.5,
        },
        labelStyle: { fill: "var(--foreground)", fontSize: 11 },
        labelBgStyle: { fill: "var(--card)" },
      })) ?? [],
    [graph.data?.edges, selected],
  );
  const edge = graph.data?.edges.find((item) => item.id === selected);
  function open(node: GraphNode) {
    if (node.kind === "card" || node.kind === "document") {
      if (node.card_id) onCard(node.card_id);
    } else onEntity(node.canonical_id || node.id);
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
              setHops(1);
              setSelected(undefined);
            }}
          >
            直接关联
          </Button>
          <Button
            variant={hops === 2 ? "default" : "outline"}
            aria-pressed={hops === 2}
            onClick={() => {
              setHops(2);
              setSelected(undefined);
            }}
          >
            再追查一层
          </Button>
        </div>
      </header>
      <p className="research-provenance">
        点击人物、事件或关系查看来源。关系线索本身不代替原文依据。
      </p>
      {graph.isPending && <p role="status">正在读取关系与证据…</p>}
      {graph.isError && (
        <p role="alert">
          {graph.error.message}
          <Button onClick={() => void graph.refetch()}>重新读取关系</Button>
        </p>
      )}
      {graph.data && (
        <>
          {graph.data.edges.length ? (
            <div className="research-graph-canvas">
              <ReactFlow
                nodes={nodes}
                edges={edges}
                fitView
                fitViewOptions={{ padding: 0.3, maxZoom: 1 }}
                minZoom={0.15}
                maxZoom={2}
                nodesDraggable={false}
                nodesConnectable={false}
                edgesReconnectable={false}
                connectOnClick={false}
                deleteKeyCode={null}
                onNodeClick={(_, node) => {
                  const item = graph.data.nodes.find(
                    (item) => item.id === node.id,
                  );
                  if (item) open(item);
                }}
                onEdgeClick={(_, edge) => setSelected(edge.id)}
              >
                <Background gap={22} color="#dddcd6" />
                <Controls showInteractive={false} />
              </ReactFlow>
            </div>
          ) : (
            <p>当前尚无已核验的关系记录。</p>
          )}
          {graph.data.truncated && (
            <p className="workspace-status-note">
              关系较多，本次只展示前 {graph.data.limit}{" "}
              条；可沿相关条目继续追查。
            </p>
          )}
          <div className="research-graph-list">
            <h4>关系中的人物与材料</h4>
            {graph.data.nodes.map((node) => (
              <Button
                variant="ghost"
                key={node.id}
                onClick={() => open(node)}
                disabled={
                  node.id === entityId ||
                  (node.kind === "card" && !node.card_id)
                }
              >
                {node.name}
              </Button>
            ))}
          </div>
          {!!graph.data.edges.length && (
            <div className="research-graph-list">
              <h4>核对关系依据</h4>
              {graph.data.edges.map((item) => (
                <Button
                  variant="outline"
                  key={item.id}
                  aria-label={`查看关系依据 · ${relationLabel(item.predicate, item.kind)}`}
                  aria-pressed={selected === item.id}
                  onClick={() => setSelected(item.id)}
                >
                  {relationLabel(item.predicate, item.kind)}
                </Button>
              ))}
            </div>
          )}
          {edge && (
            <section className="research-selected-relation">
              <h4>{relationLabel(edge.predicate, edge.kind)}</h4>
              <p>{edge.reason}</p>
              <QuestionPicker
                key={edge.id}
                material={{
                  kind: "relation",
                  target_id: edge.id,
                  title: relationLabel(edge.predicate, edge.kind),
                }}
              />
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
