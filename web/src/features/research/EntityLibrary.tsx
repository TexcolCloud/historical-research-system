import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { research, type ResearchNetworkNode, type ResearchPage } from "./api";
import { entityLabels } from "./labels";

const kinds: Record<string, string> = {
  "": "全部实体",
  ...entityLabels,
};

export default function EntityLibrary({
  compact = false,
  bookId = "",
  onSelect,
}: {
  compact?: boolean;
  bookId?: string;
  onSelect?: (id: string) => void;
}) {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const kind = Object.hasOwn(kinds, params.get("kind") ?? "")
    ? (params.get("kind") ?? "")
    : "";
  const requested = Number(params.get("offset"));
  const offset =
    Number.isSafeInteger(requested) && requested > 0 ? requested : 0;
  const [text, setText] = useState(q);
  useEffect(() => setText(q), [q]);
  const result = useQuery<
    ResearchPage<
      Pick<
        ResearchNetworkNode,
        "id" | "name" | "kind" | "source_title" | "card_count"
      >
    >
  >({
    queryKey: ["research", "network-entities", q, kind, offset, bookId],
    queryFn: ({ signal }) =>
      research.networkEntities(q, kind, bookId, offset, signal),
    staleTime: 30000,
    refetchOnWindowFocus: false,
  });
  function change(values: Record<string, string>) {
    const next = new URLSearchParams(params);
    if (!onSelect) next.delete("entity");
    next.delete("card");
    for (const [key, value] of Object.entries(values))
      value ? next.set(key, value) : next.delete(key);
    setParams(next);
  }
  return (
    <section className="research-search" data-compact={compact}>
      <div className="platform-heading">
        <div>
          {compact ? (
            <h2>查找实体</h2>
          ) : (
            <>
              <h1>实体库</h1>
              <p>按人物、机构、地点、文献和事件查看已核验卡片的来源记载。</p>
            </>
          )}
        </div>
        {!compact && (
          <Link className="research-source-link" to="/research?view=network">
            关系与时间线 →
          </Link>
        )}
      </div>
      <form
        className="research-search-form research-entity-filter"
        onSubmit={(event) => {
          event.preventDefault();
          change({ q: text.trim(), offset: "" });
          if (text.trim() === q) void result.refetch();
        }}
      >
        <Input
          aria-label="筛选实体"
          placeholder="输入名称或别名"
          value={text}
          onChange={(event) => setText(event.target.value)}
          maxLength={1000}
        />
        <label>
          实体类型
          <select
            aria-label="实体类型"
            value={kind}
            onChange={(event) =>
              change({ kind: event.target.value, offset: "" })
            }
          >
            {Object.entries(kinds).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <Button type="submit">筛选</Button>
        <Button
          type="button"
          variant="ghost"
          disabled={result.isFetching}
          onClick={() => void result.refetch()}
        >
          刷新实体
        </Button>
      </form>
      {result.isPending && <p role="status">正在读取实体…</p>}
      {result.isError && (
        <p role="alert">
          {result.error.message}
          <Button onClick={() => void result.refetch()}>重新读取实体</Button>
        </p>
      )}
      {result.data && (
        <>
          <p className="research-provenance">
            共 {result.data.total} 项 ·
            关系与别名均保留原文依据，同名不自动合并。
          </p>
          <div className="research-entity-chips">
            {result.data.items.map((entity) => (
              <Button
                key={entity.id}
                variant="outline"
                onClick={() => {
                  if (onSelect) {
                    onSelect(entity.id);
                    return;
                  }
                  const next = new URLSearchParams(params);
                  next.delete("card");
                  next.delete("returnTo");
                  next.set("entity", entity.id);
                  next.set("view", "network");
                  setParams(next);
                }}
                aria-pressed={
                  compact ? params.get("entity") === entity.id : undefined
                }
              >
                <strong>{entity.name}</strong>
                <small>{kinds[entity.kind] ?? "实体"}</small>
                {entity.source_title && (
                  <span
                    className="network-entity-source"
                    title={entity.source_title}
                  >
                    出处：{entity.source_title}
                    {entity.card_count > 1
                      ? ` · 共 ${entity.card_count} 处记载`
                      : ""}
                  </span>
                )}
              </Button>
            ))}
          </div>
          {!result.data.items.length && (
            <p>
              当前没有符合条件的已核验实体。史料卡通过审核后会自动更新实体库。
            </p>
          )}
          {result.data.total > 30 && (
            <nav className="research-pagination" aria-label="实体分页">
              <Button
                disabled={!offset || result.isFetching}
                variant="outline"
                onClick={() =>
                  change({ offset: String(Math.max(0, offset - 30)) })
                }
              >
                上一页
              </Button>
              <span>
                {offset + 1}–{Math.min(offset + 30, result.data.total)} /{" "}
                {result.data.total}
              </span>
              <Button
                disabled={offset + 30 >= result.data.total || result.isFetching}
                variant="outline"
                onClick={() => change({ offset: String(offset + 30) })}
              >
                下一页
              </Button>
            </nav>
          )}
        </>
      )}
      {!compact && (
        <details>
          <summary>历史记录</summary>
          <Link to="/research?view=questions">旧研究问题与选材（只读）</Link>
        </details>
      )}
    </section>
  );
}
