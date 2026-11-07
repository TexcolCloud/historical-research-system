import type { components } from "@/client/schema";

type Schemas = components["schemas"];
export type ResearchRun = Schemas["ResearchRunResponse"];
export type ResearchBook = Schemas["ResearchBookResponse"];

// Pydantic emits these default arrays; keep old cached responses readable too.
function normalizeEvidence(evidence: Schemas["ResearchEvidenceResponse"]) {
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
  };
}
export type ResearchCard = ReturnType<typeof normalizeCard>;
export type ResearchEvidence = ReturnType<typeof normalizeEvidence>;

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
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      signal,
      ...(body === undefined
        ? {}
        : {
            method: "POST",
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
  run: (run: string, signal?: AbortSignal) =>
    request<ResearchRun>(`/runs/${id(run)}`, signal),
  start: (book: string, revisionOf?: string) =>
    request<ResearchRun>(
      `/books/${id(book)}/runs`,
      undefined,
      revisionOf
        ? ({
            revision_of: revisionOf,
            reason: "研究来源更新，重新核验",
          } satisfies Schemas["StartResearch"])
        : {},
    ),
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
  search: (q: string, book: string, signal?: AbortSignal) => {
    const params = new URLSearchParams({ q, limit: "30" });
    if (book) params.set("book_id", book);
    return request<Schemas["ResearchSearchResponse"]>(
      `/search?${params}`,
      signal,
    );
  },
  exportHref: (card: string) => `${base}/cards/${id(card)}/export`,
};
export const researchKeys = {
  book: (book: string) => ["research", "book", book] as const,
  cards: (book: string) => ["research", "cards", book] as const,
  card: (card: string) => ["research", "card", card] as const,
  entity: (entity: string) => ["research", "entity", entity] as const,
  run: (run: string) => ["research", "run", run] as const,
};
