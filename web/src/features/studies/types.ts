import type { components } from "@/client/schema";
import type {
  ResearchEvidence,
  ResearchTimelineItem,
} from "@/features/research/api";

type Schemas = components["schemas"];
export type StudyPage<T> = {
  items: T[];
  total: number;
  offset: number;
  limit: number;
};
export type StudyDate = Schemas["StudyDate"];
export type StudyMaterial = Omit<
  Schemas["SourceReference"],
  "evidence" | "source_status" | "evidence_ids"
> & {
  evidence?: ResearchEvidence[];
  source_status?: string;
  evidence_ids: string[];
};
export type StudyTopic = Omit<
  Schemas["TopicResponse"],
  "materials" | "book_ids" | "material_count"
> & {
  materials: StudyMaterial[];
  book_ids: string[];
  material_count?: number;
};
export type StudyRecordKind = Schemas["RecordResponse"]["kind"];
export type StudyRecord = Omit<
  Schemas["RecordResponse"],
  "topic_id" | "source_refs" | "origin" | "event_status"
> & {
  topic_id: string;
  source_refs: string[];
  origin?: string;
  event_status?: string;
};
export type StudyCanvasNode = Omit<
  Schemas["CanvasNode"],
  "title" | "collapsed"
> & { title?: string; collapsed?: boolean };
export type StudyCanvasEdge = Omit<Schemas["CanvasEdge"], "notes"> & {
  notes?: string;
};
export type StudyCanvasDocument = Omit<
  Schemas["CanvasDocument"],
  "nodes" | "edges" | "viewport"
> & {
  nodes: StudyCanvasNode[];
  edges: StudyCanvasEdge[];
  viewport: NonNullable<Schemas["CanvasDocument"]["viewport"]>;
};
export type StudyCanvasRecord = Omit<
  Schemas["CanvasResponse"],
  "document" | "topic_id"
> & {
  topic_id: string;
  document: StudyCanvasDocument;
};
export type StudyCanvasSummary = Omit<Schemas["CanvasSummary"], "topic_id"> & {
  topic_id: string;
};
export type StudyCanvasRevision = Schemas["CanvasRevision"];
export type StudyCanvasContext = Omit<
  Schemas["CanvasContextResponse"],
  "records" | "materials"
> & {
  records: (StudyRecord & {
    preview_only?: Schemas["RecordPreview"]["preview_only"];
    text_truncated?: Schemas["RecordPreview"]["text_truncated"];
  })[];
  materials: StudyMaterial[];
};
type PersonalTimelineItem = Schemas["PersonalTimelineItem"];
export type StudyTimelineItem = ResearchTimelineItem | PersonalTimelineItem;
export type StudyTimeline = Omit<Schemas["StudyTimelineResponse"], "items"> & {
  items: StudyTimelineItem[];
};
export type StudyAnalysisSuggestion = Omit<
  Schemas["VerifiedSuggestion"],
  "ignored"
> & { ignored?: boolean };
export type StudyAnalysis = Omit<Schemas["AnalysisResponse"], "suggestions"> & {
  suggestions: StudyAnalysisSuggestion[];
  source_current?: boolean;
};
