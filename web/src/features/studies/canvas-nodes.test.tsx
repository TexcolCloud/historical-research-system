// @vitest-environment happy-dom
import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ReactFlow } from "@xyflow/react";
import {
  canvasNodeGeometry,
  studyNodeTypes,
  type StudyFlowNode,
} from "./canvas-nodes";

afterEach(cleanup);

test("a group keeps its full title and collapse control inside the single header row above existing children", async () => {
  const group = {
    id: "g",
    record_kind: "group" as const,
    record_id: "",
    title: "文献阅读与核对",
    x: 0,
    y: 0,
    width: 600,
    height: 400,
  };
  const nodes: StudyFlowNode[] = [
    {
      id: group.id,
      type: "study-card",
      position: { x: group.x, y: group.y },
      ...canvasNodeGeometry(group),
      data: {
        title: group.title,
        kind: "分组",
        text: "",
        meta: "",
        recordKind: "group",
        noteKind: "group",
        sourceCurrent: true,
        collapsed: false,
        onOpen: () => {},
        onToggle: () => {},
        beginGesture: () => {},
        endGesture: () => {},
      },
    },
  ];
  render(
    <ReactFlow
      nodes={nodes}
      nodeTypes={studyNodeTypes}
      width={1000}
      height={600}
    />,
  );
  const title = await screen.findByRole("heading", { name: group.title });
  const header = title.closest("article")?.querySelector("header");
  expect(header?.contains(title)).toBe(true);
  expect(
    header?.contains(
      screen.getByRole("button", { name: `折叠分组 ${group.title}` }),
    ),
  ).toBe(true);
  expect(title.getAttribute("title")).toBe(group.title);
});
