import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { research, researchKeys } from "./api";
import { researchPendingHref } from "./pending-navigation";

const kinds: Record<string, string> = {
  card: "卡片内容",
  identity: "人物身份",
  relation: "材料关系",
  coverage: "提取覆盖",
};
export default function ResearchPendingPanel() {
  const [kind, setKind] = useState(""),
    [offset, setOffset] = useState(0);
  const pending = useQuery({
    queryKey: [...researchKeys.pending, kind, offset],
    queryFn: ({ signal }) => research.pending(kind, offset, signal),
    staleTime: 10000,
    retry: false,
  });
  const items = pending.data?.items.filter((item) => !item.superseded) ?? [];
  return (
    <section className="research-pending" aria-label="研究待办">
      <header>
        <div>
          <h2>研究待办</h2>
          <p>
            逐项核对卡片、人物身份、关系和遗漏；已有最终判断的记录保留在历史版本。
          </p>
        </div>
        <Button variant="ghost" onClick={() => void pending.refetch()}>
          刷新研究待办
        </Button>
      </header>
      <div
        className="research-pending-filters"
        role="group"
        aria-label="研究待办类别"
      >
        {[["", "全部"], ...Object.entries(kinds)].map(([value, label]) => (
          <Button
            key={value}
            variant={kind === value ? "default" : "outline"}
            aria-pressed={kind === value}
            onClick={() => {
              setKind(value);
              setOffset(0);
            }}
          >
            {label}
          </Button>
        ))}
      </div>
      {pending.isPending && <p role="status">正在读取研究待办…</p>}
      {pending.isError && (
        <p role="alert">
          {pending.error.message}
          <Button onClick={() => void pending.refetch()}>
            重新读取研究待办
          </Button>
        </p>
      )}
      {pending.data && !items.length && (
        <p>当前类别没有需要人工处理的研究记录。</p>
      )}
      {items.map((item) => (
        <article className="research-result" key={item.id}>
          <span className="research-provenance">
            {kinds[item.kind] ?? "研究核验"}
          </span>
          <h3>
            <Link to={researchPendingHref(item)}>{item.title}</Link>
          </h3>
          {item.reason && <p>{item.reason}</p>}
          {!!item.issues?.length && (
            <ul>
              {item.issues.map((issue, index) => (
                <li key={index}>{issue}</li>
              ))}
            </ul>
          )}
          {item.source_current === false && (
            <p className="workspace-status-note">
              原文已更新，请先完成当前版本的研究整理。
            </p>
          )}
        </article>
      ))}
      {pending.data && pending.data.total > 50 && (
        <nav aria-label="研究待办分页" className="research-pagination">
          <Button
            variant="outline"
            disabled={offset === 0 || pending.isFetching}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            上一页
          </Button>
          <span>
            {offset + 1}–{Math.min(offset + 50, pending.data.total)} /{" "}
            {pending.data.total}
          </span>
          <Button
            variant="outline"
            disabled={offset + 50 >= pending.data.total || pending.isFetching}
            onClick={() => setOffset(offset + 50)}
          >
            下一页
          </Button>
        </nav>
      )}
    </section>
  );
}
