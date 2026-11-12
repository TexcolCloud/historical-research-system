import { expect, test } from "vitest";
import {
  groupSelection,
  ungroupSelection,
  undoCanvas,
  commitCanvas,
  createCanvasHistory,
  removeSelection,
  redoCanvas,
  orderedCanvasNodes,
  readCanvasBackup,
  reparentDroppedNodes,
  worldPosition,
  isCanvasNodeHidden,
  findCanvasPlacement,
} from "./canvas-model";

const original = {
  nodes: [
    {
      id: "a",
      record_kind: "source" as const,
      record_id: "source",
      x: 100,
      y: 120,
      width: 260,
      height: 180,
    },
    {
      id: "b",
      record_kind: "record" as const,
      record_id: "note",
      x: 440,
      y: 240,
      width: 260,
      height: 180,
    },
  ],
  edges: [
    { id: "e", source: "a", target: "b", label: "支持", relation: "support" },
  ],
  viewport: { x: 0, y: 0, z: 1 },
};

test("grouping retains world positions and undo restores exactly the prior document", () => {
  const grouped = groupSelection(original, ["a", "b"], "g");
  const group = grouped.nodes.find((node) => node.id === "g")!;
  const child = grouped.nodes.find((node) => node.id === "a")!;
  expect(child.parent_id).toBe("g");
  expect(child.x + group.x).toBe(100);
  expect(child.y + group.y).toBe(120);
  expect(grouped.edges).toEqual(original.edges);
  const history = commitCanvas(createCanvasHistory(original), grouped);
  expect(undoCanvas(history).present).toEqual(original);
  const ungrouped = ungroupSelection(grouped, ["g"]);
  expect(ungrouped.nodes.find((node) => node.id === "a")).toEqual(
    original.nodes[0],
  );
});

test("a 500-card nested canvas retains every reference and coordinate through group, collapse, restore and serialization", () => {
  const large = {
    nodes: Array.from({ length: 500 }, (_, index) => ({
      id: `n${index}`,
      record_kind: "source" as const,
      record_id: `m${index}`,
      x: (index % 25) * 320,
      y: Math.floor(index / 25) * 300,
      width: 280,
      height: 250,
    })),
    edges: Array.from({ length: 499 }, (_, index) => ({
      id: `e${index}`,
      source: `n${index}`,
      target: `n${index + 1}`,
      relation: "association",
      label: "待核关联",
    })),
    viewport: { x: 10, y: -20, z: 0.4 },
  };
  const inner = groupSelection(
    large,
    large.nodes.slice(0, 250).map((node) => node.id),
    "inner",
  );
  const outer = groupSelection(
    inner,
    ["inner", ...large.nodes.slice(250).map((node) => node.id)],
    "outer",
  );
  expect(outer.nodes).toHaveLength(502);
  expect(orderedCanvasNodes(outer)[0].id).toBe("outer");
  for (const node of outer.nodes.filter(
    (node) => node.record_kind === "source",
  )) {
    expect(worldPosition(outer, node)).toEqual(
      large.nodes.find((item) => item.id === node.id) && {
        x: large.nodes.find((item) => item.id === node.id)!.x,
        y: large.nodes.find((item) => item.id === node.id)!.y,
      },
    );
  }
  const collapsed = {
    ...outer,
    nodes: outer.nodes.map((node) =>
      node.id === "outer" ? { ...node, collapsed: true } : node,
    ),
  };
  expect(
    collapsed.nodes.filter((node) => isCanvasNodeHidden(collapsed, node)),
  ).toHaveLength(501);
  const roundTrip = readCanvasBackup(
    JSON.stringify({
      workspace_id: "w",
      topic_id: "t",
      canvas_id: "c",
      base_version: 1,
      document: collapsed,
    }),
    "w",
    "t",
    "c",
  )!;
  expect(ungroupSelection(roundTrip.document, ["inner", "outer"])).toEqual(
    large,
  );
  expect(removeSelection(collapsed, ["outer"]).nodes).toEqual([]);
  expect(large.nodes).toHaveLength(500);
});

test("deleting a group removes only its placements and attached lines, with reversible history", () => {
  const grouped = groupSelection(original, ["a", "b"], "g");
  expect(orderedCanvasNodes(grouped)[0].id).toBe("g");
  const removed = removeSelection(grouped, ["g"]);
  expect(removed.nodes).toEqual([]);
  expect(removed.edges).toEqual([]);
  const history = commitCanvas(createCanvasHistory(grouped), removed);
  expect(redoCanvas(undoCanvas(history)).present).toEqual(removed);
  expect(original.nodes[0].record_id).toBe("source");
});

test("recovery rejects another workspace, malformed coordinates and cyclic parents", () => {
  const backup = {
    workspace_id: "one",
    topic_id: "t",
    canvas_id: "c",
    base_version: 2,
    document: original,
  };
  expect(
    readCanvasBackup(JSON.stringify(backup), "one", "t", "c")?.base_version,
  ).toBe(2);
  expect(
    readCanvasBackup(JSON.stringify(backup), "other", "t", "c"),
  ).toBeNull();
  expect(
    readCanvasBackup(
      JSON.stringify({
        ...backup,
        document: { ...original, nodes: [{ ...original.nodes[0], x: null }] },
      }),
      "one",
      "t",
      "c",
    ),
  ).toBeNull();
  expect(
    readCanvasBackup(
      JSON.stringify({
        ...backup,
        document: {
          ...original,
          nodes: [
            { ...original.nodes[0], record_kind: "group", parent_id: "a" },
          ],
        },
      }),
      "one",
      "t",
      "c",
    ),
  ).toBeNull();
});

test("dropping into and outside a frame updates parent without shifting the card", () => {
  const doc = {
    ...original,
    nodes: [
      ...original.nodes,
      {
        id: "g",
        record_kind: "group" as const,
        record_id: "",
        title: "材料",
        x: 50,
        y: 50,
        width: 400,
        height: 400,
      },
    ],
  };
  const inside = reparentDroppedNodes(doc, ["a"]);
  expect(inside.nodes.find((node) => node.id === "a")).toMatchObject({
    parent_id: "g",
    x: 50,
    y: 70,
  });
  const outside = reparentDroppedNodes(
    {
      ...inside,
      nodes: inside.nodes.map((node) =>
        node.id === "a" ? { ...node, x: 900 } : node,
      ),
    },
    ["a"],
  );
  expect(outside.nodes.find((node) => node.id === "a")).toMatchObject({
    x: 950,
    y: 120,
  });
  expect(
    outside.nodes.find((node) => node.id === "a")?.parent_id,
  ).toBeUndefined();
});

test("automatic placement avoids grouped cards in world coordinates without altering existing geometry", () => {
  const document = {
    ...original,
    nodes: [
      {
        id: "g",
        record_kind: "group" as const,
        record_id: "",
        x: 300,
        y: 200,
        width: 100,
        height: 80,
      },
      { ...original.nodes[0], parent_id: "g", x: 400, y: 200 },
    ],
    edges: [],
  };
  const before = JSON.stringify(document);
  const origin = worldPosition(document, document.nodes[1]);
  const size = { width: 280, height: 250 };
  const placement = findCanvasPlacement(document, origin, size, 1100);
  for (const node of document.nodes) {
    const existing = worldPosition(document, node);
    expect(
      placement.x >= existing.x + node.width ||
        placement.x + size.width <= existing.x ||
        placement.y >= existing.y + node.height ||
        placement.y + size.height <= existing.y,
    ).toBe(true);
  }
  expect(JSON.stringify(document)).toBe(before);
});
