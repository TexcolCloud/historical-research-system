import { Link } from "react-router-dom";
import type { components } from "@/client/schema";
import { chapterHref, readingHref } from "@/features/library/reader-location";
import { sourceRoles, sourceLayers } from "@/features/library/source-roles";

type Hit = components["schemas"]["SearchHit"];
export type SourceScope = "content" | "metadata" | "all";

export function sourceScope(value: string | null): SourceScope {
  return value === "metadata" || value === "all" ? value : "content";
}

export function SourceScopeSelect({
  value,
  onChange,
}: {
  value: SourceScope;
  onChange: (value: SourceScope) => void;
}) {
  return (
    <label>
      查找内容
      <select
        aria-label="查找内容"
        value={value}
        onChange={(event) => onChange(sourceScope(event.target.value))}
      >
        <option value="content">正文、注释与图表</option>
        <option value="metadata">目录、索引与书目信息</option>
        <option value="all">全部原文（按词查找）</option>
      </select>
    </label>
  );
}

/** Clean excerpts remain leads; each separated source range has its own reading target. */
export function SearchExcerpt({
  hit,
  returnTo,
}: {
  hit: Hit;
  returnTo: string;
}) {
  const segments =
    hit.snippet_segments?.filter((part) => part.usage !== "context") ?? [];
  const text = segments.length
    ? segments.map((part) => part.text).join("")
    : (hit.snippet ?? hit.text);
  const ranges = hit.ranges?.filter((range) => range.usage === "primary") ?? [];
  const separated = ranges.some(
    (range, index) => index > 0 && range.start > ranges[index - 1].end,
  );
  const targets =
    hit.reading_targets?.filter((target) => target.usage !== "context") ?? [];
  const occurrences = hit.occurrences ?? [];
  const roleLabel = ["body", "heading", "unknown", "mixed"].includes(hit.role)
    ? ""
    : sourceRoles[hit.role];
  const ownerLabel = ["source", "unknown", "mixed"].includes(hit.layer)
    ? ""
    : sourceLayers[hit.layer];
  return (
    <div className="search-source-excerpt">
      {(roleLabel || ownerLabel) && (
        <p className="search-result-provenance">
          {[roleLabel, ownerLabel].filter(Boolean).join(" · ")}
        </p>
      )}
      <p className="search-result-excerpt">{text}</p>
      {separated && (
        <p className="search-result-provenance">
          摘要由多个原文片段组成，引用前请分别核对。
        </p>
      )}
      {targets.length > 1 && (
        <ol aria-label="定位原文片段" className="search-fragment-links">
          {targets.map((target, index) => (
            <li key={`${target.chapter_id}:${target.start}:${target.end}`}>
              <Link to={readingHref(hit.book_id, target, { returnTo })}>
                片段 {index + 1} · PDF 第 {target.physical_page} 页
              </Link>
            </li>
          ))}
        </ol>
      )}
      {!!hit.context?.length && (
        <details className="search-source-context">
          <summary>查看相关上下文、表头与注释</summary>
          {hit.context.map((part) => (
            <div key={`${part.start}:${part.end}`}>
              {part.role === "unassigned_context" ? (
                <p>附近原文 · 归属尚未确定</p>
              ) : (
                part.layer &&
                !["source", "unknown", "mixed"].includes(part.layer) && (
                  <p>{sourceLayers[part.layer] ?? part.layer}</p>
                )
              )}
              <p className="search-result-excerpt">{part.text}</p>
              {part.outside_scope && <p>关联上下文 · 位于所选目录范围外</p>}
              {part.reading_target && (
                <Link
                  to={readingHref(hit.book_id, part.reading_target, {
                    returnTo,
                  })}
                >
                  阅读这段上下文
                </Link>
              )}
              <Link
                to={chapterHref(hit.book_id, hit.chapter_id, {
                  page: part.pages[0],
                  returnTo,
                })}
              >
                查看原件第 {part.pages.join("、")} 页
              </Link>
            </div>
          ))}
        </details>
      )}
      {hit.context_truncated && <p>部分上下文较长，请打开章节继续阅读。</p>}
      {occurrences.length > 1 && (
        <details className="search-occurrences">
          <summary>相同文字的 {occurrences.length} 处来源</summary>
          <p>同文合并展示，各出处与版本保留；出现次数不代表独立佐证。</p>
          <ul>
            {occurrences.map((source) => (
              <li key={`${source.run_id}:${source.id}`}>
                {source.reading_target ? (
                  <Link
                    to={readingHref(source.book_id, source.reading_target, {
                      returnTo,
                    })}
                  >
                    {source.book_title || source.title} · PDF 第{" "}
                    {source.pages.join("、")} 页
                  </Link>
                ) : (
                  <span>
                    {source.book_title || source.title} · PDF 第{" "}
                    {source.pages.join("、")} 页
                  </span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
