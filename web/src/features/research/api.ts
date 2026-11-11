import type { components } from "@/client/schema";

type Schemas = components["schemas"];
export function isCardOnly(value: unknown): boolean {
  return (
    typeof value === "object" &&
    value !== null &&
    (("card_only" in value && value.card_only === true) ||
      ("processing_mode" in value && value.processing_mode === "source_cards"))
  );
}
export type ResearchRun = Schemas["ResearchRunResponse"];
export type ResearchBook = Schemas["ResearchBookResponse"];
export type ResearchSearchResult = Omit<
  Schemas["ResearchSearchResponse"],
  "research_backend"
> & {
  research_backend?:
    "hybrid" | "lexical" | "sql_literal" | "sql_literal_fallback";
  research_available?: boolean;
  research_index?: { state: "ready" | "building" | "partial" | "unavailable" };
};
type NotebookEvidence = {
  quote?: string;
  book_id?: string;
  reading_target?: Schemas["ReadingTarget"] | null;
  source_current?: boolean;
};
export type ResearchSelection = {
  id: string;
  kind: "source" | "card" | "entity" | "relation";
  target_id: string;
  title: string;
  decision: "saved" | "adopted" | "excluded";
  note: string;
  evidence: NotebookEvidence[];
  source_current?: boolean;
  record_current?: boolean;
  record_ref?: {
    kind: "card" | "entity" | "relation";
    id: string;
    version?: number | null;
    sha256?: string | null;
  } | null;
  version_refs?: Array<{ card_id: string; card_version: number }>;
};
export type ResearchQuestion = {
  id: string;
  title: string;
  book_ids: string[];
  notes: string;
  status: "open" | "researching" | "ready" | "resolved" | "archived";
  version: number;
  definition_version: number;
  selections: ResearchSelection[];
  findings: Array<{
    id?: string;
    text: string;
    status: string;
    unresolved: string[];
    card_id?: string;
    card_version?: number;
    definition_version: number;
    evidence_ids: string[];
    evidence?: NotebookEvidence[];
    source_current?: boolean;
    card_current?: boolean;
  }>;
  runs: Array<{ run_id: string; state?: string; book_id?: string }>;
};
type ResearchQuestionSummary = Pick<
  ResearchQuestion,
  | "id"
  | "title"
  | "book_ids"
  | "notes"
  | "status"
  | "version"
  | "definition_version"
> & { selection_count?: number; finding_count?: number; run_count?: number };
export type ResearchPage<T> = {
  items: T[];
  total: number;
  offset: number;
  limit: number;
};
export type ResearchPending = {
  id: string;
  kind: string;
  title: string;
  reason?: string;
  issues?: string[];
  card_id?: string;
  entity_id?: string;
  target_id?: string;
  book_id?: string;
  source_current?: boolean;
  version?: number;
  superseded?: boolean;
};
export type ResearchReading = {
  chapter_id: string;
  run_id: string;
  text_sha256: string;
  source_current?: boolean;
  cards: Array<Schemas["ResearchCardSummary"]>;
  annotations: Array<{
    id: string;
    entity_id: string;
    kind: string;
    name: string;
    start: number;
    end: number;
    quote: string;
    text_sha256: string;
    card_id: string;
  }>;
};
export type ResearchGraphData = {
  entity_id: string;
  hops: number;
  limit: number;
  truncated: boolean;
  nodes: Array<{
    id: string;
    name: string;
    kind: string;
    card_id?: string;
    book_id?: string;
    canonical_id?: string;
  }>;
  edges: Schemas["ResearchRelationshipResponse"][];
};

export type ResearchNetworkNode = Schemas["ResearchNetworkNode"];
export type ResearchNetworkEdge = Schemas["ResearchNetworkEdge"] & {
  directed?: boolean;
};
export type ResearchNetworkData = Schemas["ResearchNetworkResponse"];
export type ResearchTimelineItem = Schemas["ResearchTimelineItem"];
export type ResearchTimelineData = Schemas["ResearchTimelineResponse"];
export type ResearchNetworkScope = {
  bookId: string;
  dateFrom: string;
  dateTo: string;
  includeUndated: boolean;
  nodeKind: string;
  predicate: string;
  includeSources: boolean;
};

// Pydantic emits these default arrays; keep old cached responses readable too.
export function normalizeEvidence(
  evidence: Schemas["ResearchEvidenceResponse"],
) {
  return {
    ...evidence,
    pages: evidence.pages ?? [],
    footnote_ids: evidence.footnote_ids ?? [],
  };
}
function normalizeCard(card: Schemas["ResearchCardResponse"]) {
  return {
    ...card,
    evidence: card.evidence.map(normalizeEvidence),
    items: (card.items ?? []).map((item) => ({
      ...item,
      evidence_ids: item.evidence_ids ?? [],
    })),
    relations: (card.relations ?? []).map((relation) => ({
      ...relation,
      evidence_ids: relation.evidence_ids ?? [],
    })),
    tags: card.tags ?? [],
    limitations: card.limitations ?? [],
    alternatives: card.alternatives ?? [],
    questions: card.questions ?? [],
    checks: card.checks && { ...card.checks, issues: card.checks.issues ?? [] },
  };
}
function normalizeEntity(entity: Schemas["ResearchEntityResponse"]) {
  return {
    ...entity,
    evidence: (entity.evidence ?? []).map(normalizeEvidence),
    aliases: entity.aliases ?? [],
    historical_aliases: entity.historical_aliases ?? [],
    withdrawn_aliases: entity.withdrawn_aliases ?? [],
    event_status:
      typeof entity.event_status === "string" ? entity.event_status : "unknown",
    status_note:
      typeof entity.status_note === "string" ? entity.status_note : "",
    source_mentions: entity.source_mentions ?? [],
    participations: entity.participations ?? [],
    events: entity.events ?? [],
    relationships: entity.relationships ?? [],
    identity_decisions: entity.identity_decisions ?? [],
  };
}
export type ResearchCard = ReturnType<typeof normalizeCard>;
export type ResearchEvidence = ReturnType<typeof normalizeEvidence>;
export type ResearchEntity = ReturnType<typeof normalizeEntity>;
export type CardNotes = Schemas["CardNotesResponse"];

export class ResearchError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const base = "/api/v2/research";
/** Same-origin API with cancellation; mutations never retry without a user action. */
async function request<T>(
  path: string,
  signal?: AbortSignal,
  body?: unknown,
  method: "POST" | "PATCH" = "POST",
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      signal,
      ...(body === undefined
        ? {}
        : {
            method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new Error(
      body === undefined
        ? "无法连接本机服务，请重新读取。"
        : "连接中断，尚未确认操作结果。请重新读取后再重试。",
    );
  }
  if (!response.ok) {
    if (response.status === 404)
      throw new ResearchError("研究记录不存在，所属书籍可能已删除。", 404);
    const data = await response.json().catch(() => null);
    const message =
      typeof data?.detail === "string" ? data.detail : data?.detail?.message;
    throw new ResearchError(
      message ||
        (response.status === 409
          ? "资料或任务已更新，请重新读取后再提交。"
          : "操作尚未完成，请重试。"),
      response.status,
    );
  }
  return response.json() as Promise<T>;
}
const id = encodeURIComponent;
function networkScopeParams(scope: ResearchNetworkScope) {
  const params = new URLSearchParams({
    include_undated: String(scope.includeUndated),
  });
  if (scope.bookId) params.append("book_ids", scope.bookId);
  if (scope.dateFrom) params.set("date_from", scope.dateFrom);
  if (scope.dateTo) params.set("date_to", scope.dateTo);
  return params;
}
export const research = {
  book: (book: string, signal?: AbortSignal) =>
    request<ResearchBook>(
      `/books/${id(book)}?include_cards=false&include_entities=false`,
      signal,
    ),
  cards: (book: string, offset = 0, signal?: AbortSignal) =>
    request<Schemas["ResearchCardPage"]>(
      `/books/${id(book)}/cards?offset=${offset}&limit=50`,
      signal,
    ),
  card: (card: string, signal?: AbortSignal) =>
    request<Schemas["ResearchCardResponse"]>(`/cards/${id(card)}`, signal).then(
      normalizeCard,
    ),
  entity: (entity: string, signal?: AbortSignal) =>
    request<Schemas["ResearchEntityResponse"]>(
      `/entities/${id(entity)}`,
      signal,
    ).then(normalizeEntity),
  catalogue: (q: string, kind: string, signal?: AbortSignal, offset = 0) =>
    request<Schemas["ResearchEntityPage"]>(
      `/entities?${new URLSearchParams({ q, kind, limit: "30", offset: String(offset) })}`,
      signal,
    ),
  timeline: async (entity: string, signal?: AbortSignal) => {
    const data = await request<{
      items: Schemas["ResearchEntityResponse"][];
      truncated: boolean;
    }>(
      `/timeline?${new URLSearchParams({ entity_id: entity, limit: "100" })}`,
      signal,
    );
    return { ...data, items: data.items.map(normalizeEntity) };
  },
  identityDecision: (entity: string, body: Schemas["IdentityDecision"]) =>
    request<Schemas["ResearchEntityResponse"]>(
      `/entities/${id(entity)}/decisions`,
      undefined,
      body,
    ).then(normalizeEntity),
  graph: (entity: string, hops: number, signal?: AbortSignal) =>
    request<ResearchGraphData>(
      `/graph?${new URLSearchParams({ entity_id: entity, hops: String(hops), limit: "50" })}`,
      signal,
    ),
  network: (
    entity: string,
    hops: number,
    limit: number,
    scope: ResearchNetworkScope,
    signal?: AbortSignal,
  ) => {
    const params = networkScopeParams(scope);
    params.set("entity_id", entity);
    params.set("hops", String(hops));
    params.set("limit", String(limit));
    params.set("include_source_cards", String(scope.includeSources));
    if (scope.nodeKind) params.append("node_kinds", scope.nodeKind);
    if (scope.predicate) params.set("predicate", scope.predicate);
    return request<ResearchNetworkData>(`/network?${params}`, signal);
  },
  networkRelation: (edge: string, signal?: AbortSignal) =>
    request<Schemas["ResearchRelationshipResponse"]>(
      `/network/relations/${id(edge)}`,
      signal,
    ),
  networkEntities: (
    q: string,
    kind: string,
    bookId: string,
    offset: number,
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams({
      q,
      offset: String(offset),
      limit: "30",
    });
    if (kind) params.set("kind", kind);
    if (bookId) params.append("book_ids", bookId);
    return request<ResearchPage<ResearchNetworkNode>>(
      `/network/entities?${params}`,
      signal,
    );
  },
  timelinePage: (
    entity: string,
    basis: "event" | "formation",
    offset: number,
    scope: ResearchNetworkScope,
    signal?: AbortSignal,
  ) => {
    const params = networkScopeParams(scope);
    params.set("entity_id", entity);
    params.set("summary", "true");
    params.set("date_basis", basis);
    params.set("offset", String(offset));
    params.set("limit", "20");
    return request<ResearchTimelineData>(`/timeline?${params}`, signal);
  },
  run: (run: string, signal?: AbortSignal) =>
    request<ResearchRun>(`/runs/${id(run)}`, signal),
  control: (run: string, action: "pause" | "resume") =>
    request<ResearchRun>(`/runs/${id(run)}/${action}`, undefined, {}),
  decision: (
    card: string,
    action: "accept" | "edit" | "reject",
    version: number,
    text?: string,
  ) =>
    request<Schemas["ResearchCardResponse"]>(
      `/cards/${id(card)}/decision`,
      undefined,
      {
        action,
        expected_version: version,
        ...(text === undefined ? {} : { text }),
      } satisfies Schemas["ResearchDecision"],
    ).then(normalizeCard),
  search: (q: string, book: string | string[], signal?: AbortSignal) => {
    const params = new URLSearchParams({ q, limit: "30" });
    if (Array.isArray(book))
      book.forEach((value) => params.append("book_ids", value));
    else if (book) params.set("book_id", book);
    return request<ResearchSearchResult>(`/search?${params}`, signal);
  },
  pending: (kind = "", offset = 0, signal?: AbortSignal) =>
    request<ResearchPage<ResearchPending>>(
      `/pending?${new URLSearchParams({ kind, offset: String(offset), limit: "50" })}`,
      signal,
    ),
  reading: (chapter: string, signal?: AbortSignal) =>
    request<ResearchReading>(`/reading/${id(chapter)}`, signal),
  reviseCard: (
    card: string,
    version: number,
    draft: Schemas["CardRevision"]["card"],
  ) =>
    request<ResearchRun>(`/cards/${id(card)}/revisions`, undefined, {
      expected_version: version,
      card: draft,
    }),
  editableCard: (card: string, signal?: AbortSignal) =>
    request<Schemas["EditableCardResponse"]>(
      `/cards/${id(card)}/draft`,
      signal,
    ),
  notes: (card: string, signal?: AbortSignal) =>
    request<CardNotes>(`/cards/${id(card)}/notes`, signal),
  updateNotes: (card: string, expected_version: number, text: string) =>
    request<CardNotes>(
      `/cards/${id(card)}/notes`,
      undefined,
      { expected_version, text },
      "PATCH",
    ),
  questions: (signal?: AbortSignal) =>
    request<ResearchPage<ResearchQuestionSummary>>(
      "/questions?offset=0&limit=100",
      signal,
    ),
  question: (question: string, signal?: AbortSignal) =>
    request<ResearchQuestion>(`/questions/${id(question)}`, signal),
  exportHref: (card: string) => `${base}/cards/${id(card)}/export`,
};
export const researchKeys = {
  book: (book: string) => ["research", "book", book] as const,
  cards: (book: string) => ["research", "cards", book] as const,
  card: (card: string) => ["research", "card", card] as const,
  entity: (entity: string) => ["research", "entity", entity] as const,
  run: (run: string) => ["research", "run", run] as const,
  pending: ["research", "pending"] as const,
  questions: ["research", "questions"] as const,
  question: (question: string) => ["research", "question", question] as const,
};
