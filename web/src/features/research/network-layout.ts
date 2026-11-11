import dagre from "@dagrejs/dagre";

export type NetworkPosition = { x: number; y: number };
const NETWORK_NODE_LIMIT = 80;
export const NETWORK_NODE_WIDTH = 190;
export const NETWORK_NODE_HEIGHT = 92;

/** Dagre places a bounded graph; expansion keeps the reader's existing landmarks. */
export function layoutNetwork(
  nodes: ReadonlyArray<{ id: string }>,
  edges: ReadonlyArray<{ source_id: string; target_id: string }>,
  centerId: string,
  previous: ReadonlyMap<string, NetworkPosition> = new Map(),
) {
  const ordered = [...nodes]
    .sort((a, b) => {
      if (a.id === centerId) return -1;
      if (b.id === centerId) return 1;
      return (
        Number(previous.has(b.id)) - Number(previous.has(a.id)) ||
        a.id.localeCompare(b.id)
      );
    })
    .slice(0, NETWORK_NODE_LIMIT);
  const ids = new Set(ordered.map((node) => node.id));
  const graph = new dagre.graphlib.Graph()
    .setGraph({
      rankdir: "LR",
      nodesep: 34,
      ranksep: 110,
      marginx: 24,
      marginy: 24,
    })
    .setDefaultEdgeLabel(() => ({}));
  for (const node of ordered)
    graph.setNode(node.id, {
      width: NETWORK_NODE_WIDTH,
      height: NETWORK_NODE_HEIGHT,
    });
  for (const edge of [...edges].sort((a, b) =>
    `${a.source_id}:${a.target_id}`.localeCompare(
      `${b.source_id}:${b.target_id}`,
    ),
  ))
    if (ids.has(edge.source_id) && ids.has(edge.target_id))
      graph.setEdge(edge.source_id, edge.target_id);
  dagre.layout(graph);
  const positions = new Map<string, NetworkPosition>();
  for (const node of ordered) {
    const retained = previous.get(node.id);
    if (retained) positions.set(node.id, retained);
  }
  for (const node of ordered) {
    if (positions.has(node.id)) continue;
    const placed = graph.node(node.id);
    const position = {
      x: placed.x - NETWORK_NODE_WIDTH / 2,
      y: placed.y - NETWORK_NODE_HEIGHT / 2,
    };
    while (
      [...positions.values()].some(
        (other) =>
          Math.abs(other.x - position.x) < NETWORK_NODE_WIDTH + 24 &&
          Math.abs(other.y - position.y) < NETWORK_NODE_HEIGHT + 24,
      )
    )
      position.y += NETWORK_NODE_HEIGHT + 34;
    positions.set(node.id, position);
  }
  return positions;
}
