import type { components } from "@/client/schema";

/** Describe only a match visible in the returned source; semantic relevance is not a quote. */
export function matchBasis(
  hit: Pick<
    components["schemas"]["SearchHit"],
    "text" | "title" | "section_path" | "toc_path"
  > & { context?: { text: string }[] },
  query: string,
) {
  const normalize = (value: string) =>
    value.normalize("NFKC").toLocaleLowerCase().replace(/\s+/gu, "");
  const needle = normalize(query);
  const contains = (value: string) =>
    Boolean(needle) && normalize(value).includes(needle);
  if (contains(hit.text)) return "原文片段含查询词";
  if (
    [hit.title, ...(hit.section_path ?? []), ...(hit.toc_path ?? [])].some(
      contains,
    )
  )
    return "标题或目录相关";
  if (hit.context?.some((part) => contains(part.text)))
    return "关联注释或上下文含查询词";
  return "相关内容 · 请结合原文判断";
}
