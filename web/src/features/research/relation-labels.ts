export const crossRelationLabels = {
  reply_to: "回复文书",
  reprint_of: "转载版本",
  same_source: "同源材料",
  support: "支持",
  counter: "反证",
  limit: "限制",
  background: "背景",
  association: "关联",
};

const relationLabels: Readonly<Record<string, string>> = {
  ...crossRelationLabels,
  mentioned_in: "见于材料",
  participant: "参与者",
  same_as: "同一身份",
  possible_same_as: "可能为同一身份",
};

/** Translate only owned relation codes; source-authored predicates remain verbatim. */
export function relationLabel(predicate?: string | null, kind?: string | null) {
  const value = predicate?.trim() ? predicate : kind || "";
  const code = value.trim();
  return Object.hasOwn(relationLabels, code)
    ? relationLabels[code]
    : value || "关联";
}
