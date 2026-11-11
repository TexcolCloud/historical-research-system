import { research, type ResearchNetworkScope } from "./api";

export type NetworkSelection = {
  kind: "entity" | "relation" | "event" | "document";
  id: string;
};

export function networkParams(
  current: URLSearchParams,
  changes: Record<string, string>,
) {
  const next = new URLSearchParams(current);
  next.set("view", "network");
  for (const [key, value] of Object.entries(changes))
    value ? next.set(key, value) : next.delete(key);
  return next;
}

export function networkState(params: URLSearchParams) {
  const entityId = params.get("entity") ?? "";
  const requestedOffset = Number(params.get("timeline_offset"));
  const kind = params.get("selection");
  const selection: NetworkSelection = {
    kind:
      kind === "relation" || kind === "event" || kind === "document"
        ? kind
        : "entity",
    id: params.get("selected") || entityId,
  };
  const scope: ResearchNetworkScope = {
    bookId: params.get("book_id") ?? "",
    dateFrom: params.get("date_from") ?? "",
    dateTo: params.get("date_to") ?? "",
    includeUndated: params.get("undated") !== "false",
    nodeKind: params.get("node_kind") ?? "",
    predicate: params.get("predicate") ?? "",
    includeSources: params.get("sources") === "true",
  };
  return {
    entityId,
    scope,
    selection,
    display:
      params.get("display") === "timeline"
        ? ("timeline" as const)
        : ("graph" as const),
    dateBasis:
      params.get("date_basis") === "formation"
        ? ("formation" as const)
        : ("event" as const),
    hops: params.get("hops") === "2" ? (2 as const) : (1 as const),
    limit: [30, 60, 100, 200].includes(Number(params.get("network_limit")))
      ? Number(params.get("network_limit"))
      : 30,
    offset:
      Number.isSafeInteger(requestedOffset) &&
      requestedOffset > 0 &&
      requestedOffset <= 2_000_000_000
        ? requestedOffset
        : 0,
  };
}

export function entityWorkspaceHref(
  entityId: string,
  bookId = "",
  display = "graph",
) {
  return `/research?${networkParams(new URLSearchParams(), { entity: entityId, book_id: bookId, display })}`;
}

export function networkOptions(
  entity: string,
  hops: number,
  limit: number,
  scope: ResearchNetworkScope,
) {
  return {
    queryKey: ["research", "network", entity, hops, limit, scope] as const,
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      research.network(entity, hops, limit, scope, signal),
    staleTime: 30_000,
    retry: false,
    refetchOnWindowFocus: false,
  };
}

export function timelineOptions(
  entity: string,
  basis: "event" | "formation",
  offset: number,
  scope: ResearchNetworkScope,
) {
  return {
    queryKey: [
      "research",
      "timeline-page",
      entity,
      basis,
      offset,
      scope.bookId,
      scope.dateFrom,
      scope.dateTo,
      scope.includeUndated,
    ] as const,
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      research.timelinePage(entity, basis, offset, scope, signal),
    staleTime: 30_000,
    retry: false,
    refetchOnWindowFocus: false,
  };
}
