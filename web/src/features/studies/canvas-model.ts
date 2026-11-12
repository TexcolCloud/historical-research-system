import type { StudyCanvasDocument, StudyCanvasNode } from "./types";

export type CanvasHistory = {
  past: StudyCanvasDocument[];
  present: StudyCanvasDocument;
  future: StudyCanvasDocument[];
};

export function createCanvasHistory(
  document: StudyCanvasDocument,
): CanvasHistory {
  return { past: [], present: document, future: [] };
}

export function commitCanvas(
  history: CanvasHistory,
  next: StudyCanvasDocument,
): CanvasHistory {
  if (JSON.stringify(history.present) === JSON.stringify(next)) return history;
  return {
    past: [...history.past.slice(-49), history.present],
    present: next,
    future: [],
  };
}

export function undoCanvas(history: CanvasHistory): CanvasHistory {
  const previous = history.past.at(-1);
  if (!previous) return history;
  return {
    past: history.past.slice(0, -1),
    present: { ...previous, viewport: history.present.viewport },
    future: [history.present, ...history.future],
  };
}

export function redoCanvas(history: CanvasHistory): CanvasHistory {
  const next = history.future[0];
  if (!next) return history;
  return {
    past: [...history.past, history.present],
    present: { ...next, viewport: history.present.viewport },
    future: history.future.slice(1),
  };
}

export function worldPosition(
  document: StudyCanvasDocument,
  node: StudyCanvasNode,
) {
  let x = node.x,
    y = node.y;
  const visited = new Set([node.id]);
  let parent = document.nodes.find((item) => item.id === node.parent_id);
  while (parent && !visited.has(parent.id)) {
    visited.add(parent.id);
    x += parent.x;
    y += parent.y;
    parent = document.nodes.find((item) => item.id === parent?.parent_id);
  }
  return { x, y };
}

/** Find an empty shelf near the current view without moving any existing placement. */
export function findCanvasPlacement(
  document: StudyCanvasDocument,
  origin: { x: number; y: number },
  size: { width: number; height: number },
  rightEdge: number,
) {
  const gap = 24;
  const occupied = document.nodes
    .filter((node) => !isCanvasNodeHidden(document, node))
    .map((node) => ({
      ...worldPosition(document, node),
      width: node.width,
      height: node.collapsed ? 82 : node.height,
    }));
  const right = Math.max(origin.x + size.width, rightEdge);
  let y = origin.y;
  for (let row = 0; row <= occupied.length; row++) {
    let x = origin.x;
    let nextRow = Infinity;
    for (let column = 0; column <= occupied.length; column++) {
      const collisions = occupied.filter(
        (box) =>
          x < box.x + box.width + gap &&
          x + size.width + gap > box.x &&
          y < box.y + box.height + gap &&
          y + size.height + gap > box.y,
      );
      if (!collisions.length) return { x, y };
      nextRow = Math.min(
        nextRow,
        ...collisions.map((box) => box.y + box.height + gap),
      );
      x = Math.max(...collisions.map((box) => box.x + box.width + gap));
      if (x + size.width > right) break;
    }
    y = nextRow;
  }
  return {
    x: origin.x,
    y: Math.max(origin.y, ...occupied.map((box) => box.y + box.height + gap)),
  };
}

/** Group roots only; descendants retain their own local geometry. */
export function groupSelection(
  document: StudyCanvasDocument,
  selected: string[],
  id: string,
) {
  const selection = new Set(selected);
  const roots = document.nodes.filter(
    (node) =>
      selection.has(node.id) && !hasSelectedAncestor(document, node, selection),
  );
  if (!roots.length) return document;
  const bounds = roots.map((node) => ({
    ...worldPosition(document, node),
    width: node.width,
    height: node.height,
  }));
  const x = Math.min(...bounds.map((box) => box.x)) - 24;
  const y = Math.min(...bounds.map((box) => box.y)) - 48;
  const group: StudyCanvasNode = {
    id,
    record_kind: "group",
    record_id: "",
    title: "未命名分组",
    x,
    y,
    width: Math.max(...bounds.map((box) => box.x + box.width)) - x + 24,
    height: Math.max(...bounds.map((box) => box.y + box.height)) - y + 24,
  };
  const rootIds = new Set(roots.map((node) => node.id));
  return {
    ...document,
    nodes: [
      group,
      ...document.nodes.map((node) => {
        if (!rootIds.has(node.id)) return node;
        const position = worldPosition(document, node);
        return { ...node, x: position.x - x, y: position.y - y, parent_id: id };
      }),
    ],
  };
}

function hasSelectedAncestor(
  document: StudyCanvasDocument,
  node: StudyCanvasNode,
  selected: Set<string>,
) {
  const visited = new Set<string>();
  let id = node.parent_id;
  while (id && !visited.has(id)) {
    if (selected.has(id)) return true;
    visited.add(id);
    id = document.nodes.find((item) => item.id === id)?.parent_id;
  }
  return false;
}

export function ungroupSelection(
  document: StudyCanvasDocument,
  selected: string[],
) {
  const removed = new Set(
    document.nodes
      .filter(
        (node) => selected.includes(node.id) && node.record_kind === "group",
      )
      .map((node) => node.id),
  );
  if (!removed.size) return document;
  const byId = new Map(document.nodes.map((node) => [node.id, node]));
  return {
    ...document,
    nodes: document.nodes
      .filter((node) => !removed.has(node.id))
      .map((node) => {
        if (!node.parent_id || !removed.has(node.parent_id)) return node;
        let parentId = byId.get(node.parent_id)?.parent_id;
        while (parentId && removed.has(parentId))
          parentId = byId.get(parentId)?.parent_id;
        const point = worldPosition(document, node);
        const parent = parentId ? byId.get(parentId) : undefined;
        const parentPoint = parent
          ? worldPosition(document, parent)
          : { x: 0, y: 0 };
        const restored = {
          ...node,
          x: point.x - parentPoint.x,
          y: point.y - parentPoint.y,
        };
        if (parentId) return { ...restored, parent_id: parentId };
        delete restored.parent_id;
        return restored;
      }),
    edges: document.edges.filter(
      (edge) => !removed.has(edge.source) && !removed.has(edge.target),
    ),
  };
}

/** Removing a placement never deletes its source card or personal study record. */
export function removeSelection(
  document: StudyCanvasDocument,
  nodes: string[],
  edges: string[] = [],
) {
  const removed = new Set(nodes);
  let count = -1;
  while (count !== removed.size) {
    count = removed.size;
    for (const node of document.nodes)
      if (node.parent_id && removed.has(node.parent_id)) removed.add(node.id);
  }
  return {
    ...document,
    nodes: document.nodes.filter((node) => !removed.has(node.id)),
    edges: document.edges.filter(
      (edge) =>
        !edges.includes(edge.id) &&
        !removed.has(edge.source) &&
        !removed.has(edge.target),
    ),
  };
}

export function reparentDroppedNodes(
  document: StudyCanvasDocument,
  dragged: string[],
) {
  const selected = new Set(dragged);
  const roots = new Set(
    document.nodes
      .filter(
        (node) =>
          selected.has(node.id) &&
          !hasSelectedAncestor(document, node, selected),
      )
      .map((node) => node.id),
  );
  const groups = document.nodes.filter(
    (node) =>
      node.record_kind === "group" &&
      !node.collapsed &&
      !roots.has(node.id) &&
      !hasSelectedAncestor(document, node, roots),
  );
  return {
    ...document,
    nodes: document.nodes.map((node) => {
      if (!roots.has(node.id)) return node;
      const point = worldPosition(document, node);
      const center = {
        x: point.x + node.width / 2,
        y: point.y + node.height / 2,
      };
      const parent = groups
        .filter((group) => {
          const box = worldPosition(document, group);
          return (
            center.x >= box.x &&
            center.x <= box.x + group.width &&
            center.y >= box.y &&
            center.y <= box.y + group.height
          );
        })
        .sort((a, b) => a.width * a.height - b.width * b.height)[0];
      const parentPoint = parent
        ? worldPosition(document, parent)
        : { x: 0, y: 0 };
      const next = {
        ...node,
        x: point.x - parentPoint.x,
        y: point.y - parentPoint.y,
      };
      if (parent) return { ...next, parent_id: parent.id };
      delete next.parent_id;
      return next;
    }),
  };
}

export function orderedCanvasNodes(document: StudyCanvasDocument) {
  const pending = [...document.nodes],
    result: StudyCanvasNode[] = [],
    added = new Set<string>();
  while (pending.length) {
    const index = pending.findIndex(
      (node) => !node.parent_id || added.has(node.parent_id),
    );
    if (index < 0) break;
    const [node] = pending.splice(index, 1);
    result.push(node);
    added.add(node.id);
  }
  return result;
}

export function isCanvasNodeHidden(
  document: StudyCanvasDocument,
  node: StudyCanvasNode,
) {
  const visited = new Set<string>();
  let parentId = node.parent_id;
  while (parentId && !visited.has(parentId)) {
    visited.add(parentId);
    const parent = document.nodes.find((item) => item.id === parentId);
    if (parent?.collapsed) return true;
    parentId = parent?.parent_id;
  }
  return false;
}

export type CanvasBackup = {
  workspace_id: string;
  topic_id: string;
  canvas_id: string;
  base_version: number;
  document: StudyCanvasDocument;
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isDocument(value: unknown): value is StudyCanvasDocument {
  if (
    !isObject(value) ||
    !Array.isArray(value.nodes) ||
    !Array.isArray(value.edges) ||
    !isObject(value.viewport)
  )
    return false;
  if (value.nodes.length > 2000 || value.edges.length > 4000) return false;
  if (
    ![value.viewport.x, value.viewport.y, value.viewport.z].every(
      (v) => typeof v === "number" && Number.isFinite(v),
    ) ||
    Number(value.viewport.z) <= 0
  )
    return false;
  const parents = new Map<string, string | undefined>(),
    kinds = new Map<string, unknown>();
  for (const node of value.nodes) {
    if (
      !isObject(node) ||
      typeof node.id !== "string" ||
      !node.id ||
      parents.has(node.id) ||
      typeof node.record_id !== "string" ||
      !["source", "record", "group"].includes(String(node.record_kind))
    )
      return false;
    if (
      ![node.x, node.y, node.width, node.height].every(
        (v) => typeof v === "number" && Number.isFinite(v),
      ) ||
      Number(node.width) <= 0 ||
      Number(node.height) <= 0
    )
      return false;
    if (node.parent_id != null && typeof node.parent_id !== "string")
      return false;
    if (
      (node.title != null && typeof node.title !== "string") ||
      (node.collapsed != null && typeof node.collapsed !== "boolean")
    )
      return false;
    parents.set(node.id, node.parent_id || undefined);
    kinds.set(node.id, node.record_kind);
  }
  for (const [id, parent] of parents) {
    const seen = new Set([id]);
    let current = parent;
    while (current) {
      if (seen.has(current) || kinds.get(current) !== "group") return false;
      seen.add(current);
      current = parents.get(current);
    }
  }
  const edgeIds = new Set<string>();
  for (const edge of value.edges) {
    if (
      !isObject(edge) ||
      typeof edge.id !== "string" ||
      edgeIds.has(edge.id) ||
      typeof edge.source !== "string" ||
      typeof edge.target !== "string" ||
      !parents.has(edge.source) ||
      !parents.has(edge.target) ||
      typeof edge.label !== "string" ||
      typeof edge.relation !== "string"
    )
      return false;
    if (
      (edge.notes != null && typeof edge.notes !== "string") ||
      (edge.source_refs != null &&
        (!Array.isArray(edge.source_refs) ||
          edge.source_refs.some((id) => typeof id !== "string")))
    )
      return false;
    edgeIds.add(edge.id);
  }
  return true;
}

export function readCanvasBackup(
  raw: string | null,
  workspace: string,
  topic: string,
  canvas: string,
): CanvasBackup | null {
  if (!raw || raw.length > 8_000_000) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (
      !isObject(value) ||
      value.workspace_id !== workspace ||
      value.topic_id !== topic ||
      value.canvas_id !== canvas ||
      !Number.isSafeInteger(value.base_version) ||
      Number(value.base_version) < 1 ||
      !isDocument(value.document)
    )
      return null;
    return {
      workspace_id: workspace,
      topic_id: topic,
      canvas_id: canvas,
      base_version: Number(value.base_version),
      document: value.document,
    };
  } catch {
    return null;
  }
}
