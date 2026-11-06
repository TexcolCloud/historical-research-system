import dagre from "@dagrejs/dagre";

export type ExecutionNode = {
  id: string;
  kind: string;
  label: string;
  state: string;
  model?: string;
  assignment?: Record<string, unknown>;
  scope?: string;
  contexts?: string[];
  progress?: { completed: number | null; total: number | null; unit: string };
  started_at?: string;
  finished_at?: string;
  rounds?: number;
  artifacts?: string[];
  input_summary?: Record<string, unknown>;
  output_summary?: Record<string, unknown>;
  attempts?: number | Record<string, unknown>[];
  error_code?: string;
};
export type ExecutionEdge = {
  id: string;
  source: string;
  target: string;
  relation: string;
};
export type Position = { x: number; y: number };
const ownershipEdge = (edge: ExecutionEdge) =>
  ["delegates", "calls", "produces"].includes(edge.relation);

// Position slots are retained between snapshots; a new sibling never moves existing nodes.
export function layoutGraph(
  nodes: ExecutionNode[],
  edges: ExecutionEdge[],
  previous: Map<string, Position> = new Map(),
) {
  const positions = new Map(previous),
    ids = new Set(nodes.map((n) => n.id));
  if (nodes.every((node) => positions.has(node.id))) return positions;
  const graph = new dagre.graphlib.Graph()
    .setGraph({
      rankdir: "LR",
      ranksep: 115,
      nodesep: 48,
      marginx: 40,
      marginy: 40,
    })
    .setDefaultEdgeLabel(() => ({}));
  for (const node of nodes) graph.setNode(node.id, { width: 280, height: 184 });
  for (const edge of edges)
    if (ownershipEdge(edge) && ids.has(edge.source) && ids.has(edge.target))
      graph.setEdge(edge.source, edge.target);
  dagre.layout(graph);
  for (const node of nodes) {
    if (positions.has(node.id)) continue;
    const placed = graph.node(node.id),
      position = { x: placed.x - 140, y: placed.y - 92 };
    while (
      [...positions.values()].some(
        (p) =>
          Math.abs(p.x - position.x) < 300 && Math.abs(p.y - position.y) < 210,
      )
    )
      position.y += 232;
    positions.set(node.id, position);
  }
  return positions;
}
