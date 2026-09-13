import { expect, test } from "vitest";
import { executionGraph } from "./execution-graph";
test("graph creates only observed nodes and recorded parent relationships", () => {
  const nodes = [
    {
      id: "main",
      run_id: "run",
      parent_id: null,
      kind: "agent",
      label: "主任务",
      objective: "规划",
      state: "completed",
      started_at: "2026-09-13T00:00:00Z",
      finished_at: null,
    },
    {
      id: "child",
      run_id: "run",
      parent_id: "main",
      kind: "agent",
      label: "阅读",
      objective: "第一章",
      state: "running",
      started_at: "2026-09-13T00:01:00Z",
      finished_at: null,
    },
    {
      id: "service",
      run_id: "run",
      parent_id: null,
      kind: "service",
      label: "索引",
      objective: "正文",
      state: "running",
      started_at: "2026-09-13T00:01:00Z",
      finished_at: null,
    },
  ];
  const graph = executionGraph(nodes);
  expect(graph.nodes.map((node) => node.id)).toEqual([
    "main",
    "child",
    "service",
  ]);
  expect(graph.edges).toEqual([
    {
      id: "main:child",
      source: "main",
      target: "child",
      relation: "delegates",
    },
  ]);
  expect(executionGraph([])).toEqual({ nodes: [], edges: [] });
});
