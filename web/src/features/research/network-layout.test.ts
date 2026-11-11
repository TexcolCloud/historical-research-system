import { expect, test } from "vitest";
import { layoutNetwork } from "./network-layout";

test("network layout follows relationships deterministically and preserves existing positions during expansion", () => {
  const nodes = [{ id: "person" }, { id: "event" }];
  const edges = [{ source_id: "person", target_id: "event" }];
  const initial = layoutNetwork(nodes, edges, "person");
  expect(initial.get("event")!.x).toBeGreaterThan(initial.get("person")!.x);
  expect(layoutNetwork([...nodes].reverse(), edges, "person")).toEqual(initial);
  const expanded = layoutNetwork(
    [...nodes, { id: "place" }],
    [...edges, { source_id: "event", target_id: "place" }],
    "person",
    initial,
  );
  expect(expanded.get("person")).toEqual(initial.get("person"));
  expect(expanded.get("event")).toEqual(initial.get("event"));
  expect(expanded.get("place")).toBeDefined();
});

test("expanding a graph at its node cap retains all visible nodes before lexically earlier additions", () => {
  const nodes = Array.from({ length: 80 }, (_, index) => ({
    id: `z-${String(index).padStart(2, "0")}`,
  }));
  const initial = layoutNetwork(nodes, [], "z-00");
  const expanded = layoutNetwork(
    [...nodes, { id: "a-new" }],
    [],
    "z-00",
    initial,
  );
  expect(expanded.size).toBe(80);
  for (const node of nodes)
    expect(expanded.get(node.id)).toEqual(initial.get(node.id));
  expect(expanded.has("a-new")).toBe(false);
});
