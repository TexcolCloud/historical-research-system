import type { components } from "@/client/schema";
import type { ResearchCard } from "./api";

type S = components["schemas"];
export type SourceField = S["SourceValue"];
export type ReadingCard = ResearchCard;

export const certaintyLabels: Record<string, string> = {
  stated: "原文明确记载",
  inferred: "推断",
  unknown: "未明",
  disputed: "有争议",
};
export const cardStateLabel = (state: string) =>
  ({
    verified: "模型核验通过",
    pending: "待人工核对",
    human_accepted: "用户已确认",
    excluded: "已排除",
  })[state] ?? state;
export const dateLabel = (date?: S["ResearchDate"] | null) =>
  date?.text
    ? `${date.text}${date.certainty !== "stated" ? `（${certaintyLabels[date.certainty] ?? date.certainty}）` : ""}`
    : "";
export const cardNumber = (card: { id: string; display_id?: string }) =>
  card.display_id || `CARD-${card.id.slice(0, 12).toUpperCase()}`;
export const authorLabel = (card: {
  authors?: string;
  metadata?: { authors?: SourceField } | null;
}) =>
  card.authors ||
  (card.metadata?.authors?.certainty !== "unknown"
    ? card.metadata?.authors?.value
    : "") ||
  "作者未详";
export function pageRange(pages: number[] = []) {
  const values = [...new Set(pages)].sort((a, b) => a - b);
  const ranges: string[] = [];
  for (let at = 0; at < values.length; at++) {
    const first = values[at];
    while (at + 1 < values.length && values[at + 1] === values[at] + 1) at++;
    ranges.push(
      first === values[at] ? String(first) : `${first}–${values[at]}`,
    );
  }
  return ranges.join("、");
}
