import type { components } from "./schema";
import type {
  ExecutionNode,
  ExecutionEdge,
} from "../components/execution/agent-graph";
export function executionGraph(
  records: components["schemas"]["ExecutionNode"][],
): { nodes: ExecutionNode[]; edges: ExecutionEdge[] } {
  return {
    nodes: records.map((row) => ({
      id: row.id,
      label: row.label,
      kind: row.kind,
      state: row.state,
      assignment: { objective: row.objective },
      started_at: row.started_at,
      ...(row.finished_at ? { finished_at: row.finished_at } : {}),
    })),
    edges: records
      .filter(
        (row) =>
          row.parent_id &&
          records.some((parent) => parent.id === row.parent_id),
      )
      .map((row) => ({
        id: `${row.parent_id}:${row.id}`,
        source: row.parent_id!,
        target: row.id,
        relation: ["agent", "group"].includes(row.kind) ? "delegates" : "calls",
      })),
  };
}
