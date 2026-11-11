import { normalizeEvidence } from "@/features/research/api";
import type {
  StudyAnalysis,
  StudyCanvasRecord,
  StudyCanvasSummary,
  StudyCanvasDocument,
  StudyCanvasContext,
  StudyCanvasRevision,
  StudyMaterial,
  StudyPage,
  StudyRecord,
  StudyRecordKind,
  StudyTimeline,
  StudyTopic,
} from "./types";

export class StudyError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** Mutations keep the caller's version; unknown outcomes are never silently retried. */
async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v2/studies${path}`, {
      method,
      signal,
      ...(body === undefined
        ? {}
        : {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new StudyError(
      method === "GET"
        ? "无法连接本机服务，请重新读取。"
        : "连接中断，尚未确认保存结果。当前输入已保留，请重新读取后重试。",
      0,
    );
  }
  if (!response.ok) {
    const value = await response.json().catch(() => null);
    const explanation =
      typeof value?.detail === "string"
        ? value.detail
        : Array.isArray(value?.detail)
          ? value.detail
              .map((issue: { msg?: unknown }) =>
                typeof issue?.msg === "string"
                  ? issue.msg.replace(/^Value error, /, "")
                  : "",
              )
              .filter(Boolean)
              .join("；")
          : value?.detail?.message;
    throw new StudyError(
      explanation ||
        (response.status === 404
          ? "研究记录不存在或不属于当前工作区。"
          : response.status === 409
            ? "资料已有新版本，当前输入已保留，请重新读取后再保存。"
            : "操作未完成，请检查输入并重试。"),
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

export { request as studyRequest };

const id = encodeURIComponent;
type NewTopic = Pick<StudyTopic, "title"> &
  Partial<Pick<StudyTopic, "description" | "book_ids">>;
type TopicUpdate = Partial<NewTopic & Pick<StudyTopic, "archived">> & {
  expected_version: number;
};
type NewRecord = Pick<StudyRecord, "kind" | "title"> &
  Partial<
    Pick<
      StudyRecord,
      "text" | "limitations" | "source_refs" | "date" | "event_status"
    >
  >;
type RecordUpdate = Partial<
  Omit<NewRecord, "kind"> & Pick<StudyRecord, "archived">
> & { expected_version: number };
type CanvasCreate = { title: string; document?: StudyCanvasDocument };
type CanvasUpdate = Partial<
  CanvasCreate & Pick<StudyCanvasRecord, "archived">
> & { expected_version: number };
export type TimelineFilters = {
  date_basis?: "event" | "formation";
  date_from?: string;
  date_to?: string;
  include_undated?: boolean;
  material_id?: string;
  offset?: number;
  limit?: number;
};

export const studyKeys = {
  all: ["studies"] as const,
  topics: (archived = false) => ["studies", "topics", archived] as const,
  topic: (topic: string) => ["studies", "topic", topic] as const,
  records: (topic: string) => ["studies", "records", topic] as const,
  canvases: (topic: string) => ["studies", "canvases", topic] as const,
  canvas: (canvas: string) => ["studies", "canvas", canvas] as const,
  material: (topic: string, material: string) =>
    ["studies", "material", topic, material] as const,
  analyses: (topic: string) => ["studies", "analyses", topic] as const,
};

export const studies = {
  topics: (
    archived = false,
    signal?: AbortSignal,
    options: { offset?: number; limit?: number } = {},
  ) =>
    request<StudyPage<StudyTopic>>(
      `/topics?${new URLSearchParams({ archived: String(archived), offset: String(options.offset ?? 0), limit: String(options.limit ?? 100) })}`,
      "GET",
      undefined,
      signal,
    ),
  topic: (topic: string, signal?: AbortSignal) =>
    request<StudyTopic>(`/topics/${id(topic)}`, "GET", undefined, signal),
  createTopic: (body: NewTopic) => request<StudyTopic>("/topics", "POST", body),
  updateTopic: (topic: string, body: TopicUpdate) =>
    request<StudyTopic>(`/topics/${id(topic)}`, "PATCH", body),
  addMaterial: (
    topic: string,
    body: {
      expected_version: number;
      card_id: string;
      card_version: number;
      evidence_ids?: string[];
    },
  ) => request<StudyTopic>(`/topics/${id(topic)}/materials`, "POST", body),
  removeMaterial: (topic: string, material: string, expectedVersion: number) =>
    request<StudyTopic>(
      `/topics/${id(topic)}/materials/${id(material)}?expected_version=${expectedVersion}`,
      "DELETE",
    ),
  material: (topic: string, material: string, signal?: AbortSignal) =>
    request<StudyMaterial>(
      `/topics/${id(topic)}/materials/${id(material)}`,
      "GET",
      undefined,
      signal,
    ).then((value) => ({
      ...value,
      evidence: value.evidence?.map(normalizeEvidence) ?? [],
    })),
  records: (
    topic: string,
    signal?: AbortSignal,
    archived = false,
    options: { offset?: number; limit?: number; kind?: StudyRecordKind } = {},
  ) =>
    request<StudyPage<StudyRecord>>(
      `/topics/${id(topic)}/records?${new URLSearchParams({ limit: String(options.limit ?? 200), offset: String(options.offset ?? 0), archived: String(archived), ...(options.kind ? { kind: options.kind } : {}) })}`,
      "GET",
      undefined,
      signal,
    ),
  record: (record: string, signal?: AbortSignal) =>
    request<StudyRecord>(`/records/${id(record)}`, "GET", undefined, signal),
  createRecord: (topic: string, body: NewRecord) =>
    request<StudyRecord>(`/topics/${id(topic)}/records`, "POST", body),
  updateRecord: (record: string, body: RecordUpdate) =>
    request<StudyRecord>(`/records/${id(record)}`, "PATCH", body),
  canvases: (
    topic: string,
    signal?: AbortSignal,
    archived = false,
    options: { offset?: number; limit?: number } = {},
  ) =>
    request<StudyPage<StudyCanvasSummary>>(
      `/topics/${id(topic)}/canvases?${new URLSearchParams({ limit: String(options.limit ?? 100), offset: String(options.offset ?? 0), archived: String(archived) })}`,
      "GET",
      undefined,
      signal,
    ),
  canvas: (canvas: string, signal?: AbortSignal) =>
    request<StudyCanvasRecord>(
      `/canvases/${id(canvas)}`,
      "GET",
      undefined,
      signal,
    ),
  canvasContext: (canvas: string, version: number, signal?: AbortSignal) =>
    request<StudyCanvasContext>(
      `/canvases/${id(canvas)}/context?expected_version=${version}`,
      "GET",
      undefined,
      signal,
    ),
  createCanvas: (topic: string, body: CanvasCreate) =>
    request<StudyCanvasRecord>(`/topics/${id(topic)}/canvases`, "POST", body),
  updateCanvas: (canvas: string, body: CanvasUpdate) =>
    request<StudyCanvasRecord>(`/canvases/${id(canvas)}`, "PATCH", body),
  revisions: (canvas: string, signal?: AbortSignal) =>
    request<StudyCanvasRevision[]>(
      `/canvases/${id(canvas)}/revisions`,
      "GET",
      undefined,
      signal,
    ),
  exportURL: (canvas: string, format: "json" | "md") =>
    `/api/v2/studies/canvases/${id(canvas)}/export?format=${format === "md" ? "markdown" : format}`,
  timeline: (topic: string, filters: TimelineFilters, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters))
      if (value !== undefined && value !== "") params.set(key, String(value));
    return request<StudyTimeline>(
      `/topics/${id(topic)}/timeline?${params}`,
      "GET",
      undefined,
      signal,
    );
  },
  analyses: (topic: string, signal?: AbortSignal) =>
    request<StudyPage<StudyAnalysis>>(
      `/topics/${id(topic)}/analyses?limit=100`,
      "GET",
      undefined,
      signal,
    ),
  analysis: (analysis: string, signal?: AbortSignal) =>
    request<StudyAnalysis>(
      `/analyses/${id(analysis)}`,
      "GET",
      undefined,
      signal,
    ),
  createAnalysis: (
    topic: string,
    body: {
      mode: StudyAnalysis["mode"];
      material_ids: string[];
      prompt: string;
    },
  ) => request<StudyAnalysis>(`/topics/${id(topic)}/analyses`, "POST", body),
  cancelAnalysis: (analysis: string, expectedVersion: number) =>
    request<StudyAnalysis>(`/analyses/${id(analysis)}/cancel`, "POST", {
      expected_version: expectedVersion,
    }),
  adoptSuggestion: (
    analysis: string,
    body: {
      expected_version: number;
      suggestion_id: string;
      kind: "note" | "argument" | "thought";
      title?: string;
    },
  ) => request<StudyRecord>(`/analyses/${id(analysis)}/adopt`, "POST", body),
};
