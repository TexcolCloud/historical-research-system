import type { components } from "@/client/schema";

type Entry = components["schemas"]["NavigationEntry"];

const labels: Record<string, string> = {
  matched: "已关联正文",
  matched_manual: "已确认跳转",
  unlinked_manual: "已确认不设置跳转",
  waiting_evidence: "等待系统核验",
};

/** Human action classification comes from the API; this helper only describes it. */
export function navigationStatusLabel(entry: Entry) {
  if (entry.needs_confirmation)
    return entry.status === "not_found"
      ? "未找到对应正文，需要你确认"
      : "需要确认目录跳转";
  return labels[entry.status] ?? "暂无可用跳转";
}

export function navigationCompleted(entry: Entry) {
  return ["matched", "matched_manual", "unlinked_manual"].includes(
    entry.status,
  );
}
