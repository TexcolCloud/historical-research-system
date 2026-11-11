import { expect, test } from "vitest";
import {
  adoptUserNodes,
  ConnectionMode,
  getEdgePosition,
  getNodesInside,
  type InternalNodeBase,
  type ParentLookup,
} from "@xyflow/system";
import { canvasNodeGeometry } from "./canvas-nodes";

test("real canvas engine retains edge anchors and culls a 500-card controlled document after rerenders", () => {
  const document = Array.from({ length: 500 }, (_, index) => ({
    id: `node-${index}`,
    record_kind: "source" as const,
    record_id: `source-${index}`,
    x: (index % 25) * 320,
    y: Math.floor(index / 25) * 250,
    width: 280,
    height: 200,
  }));
  const lookup = new Map<string, InternalNodeBase>();
  const parents: ParentLookup = new Map();
  for (let render = 0; render < 3; render++) {
    const nodes = document.map((node) => ({
      id: node.id,
      data: {},
      position: { x: node.x + render * 5, y: node.y },
      ...canvasNodeGeometry(node),
    }));
    const result = adoptUserNodes(nodes, lookup, parents);
    expect(result.nodesInitialized).toBe(true);
    expect(
      getEdgePosition({
        id: "edge-0",
        sourceHandle: null,
        targetHandle: null,
        sourceNode: lookup.get("node-0")!,
        targetNode: lookup.get("node-1")!,
        connectionMode: ConnectionMode.Strict,
      }),
    ).toMatchObject({
      sourceX: 284.5 + render * 5,
      sourceY: 100,
      targetX: 315.5 + render * 5,
      targetY: 100,
    });
    const visible = getNodesInside(
      lookup,
      { x: 0, y: 0, width: 1000, height: 600 },
      [0, 0, 1],
      true,
    );
    expect(visible).toHaveLength(12);
  }
});
