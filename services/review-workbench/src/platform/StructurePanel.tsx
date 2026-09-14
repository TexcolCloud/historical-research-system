import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Layers3 } from "lucide-react";
import { Button } from "../components/ui/button";
import { client } from "./client";
import type { components } from "./schema";

type Item = components["schemas"]["StructureItem"];

export default function StructurePanel({
  runId,
  items,
  revision,
  onLocate,
}: {
  runId: string;
  items?: Item[];
  revision?: number;
  onLocate?: (page: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const query = useQuery({
    queryKey: ["platform", "structure", runId, revision],
    enabled: open && items === undefined,
    staleTime: 60_000,
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/runs/{run_id}/structure", {
        signal,
        params: { path: { run_id: runId } },
      });
      if (!data) throw new Error("暂时无法读取结构整理结果。");
      return data;
    },
  });
  const rows = items ?? query.data?.items;
  const ready = rows?.filter((row) => row.status === "ready").length ?? 0;
  return (
    <details
      className="platform-structure"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <Layers3 size={18} aria-hidden="true" />
        <span>结构整理结果</span>
        {rows && (
          <small>
            {ready} 项来源一致 · {rows.length - ready} 项保留原结构
          </small>
        )}
      </summary>
      {open && (
        <div className="platform-structure-body">
          <p>
            查看标题层级、跨页表格与续段关系。内容核对完成且来源一致的关系用于连续阅读；原文和出处始终保留。
          </p>
          {query.isError && (
            <p role="alert">
              {query.error.message}
              <Button variant="outline" onClick={() => void query.refetch()}>
                重试
              </Button>
            </p>
          )}
          {!rows && !query.isError && <p role="status">正在读取结构结果…</p>}
          {rows?.length === 0 && <p>暂无可展示的结构关系，仍按原文阅读。</p>}
          {rows && rows.length > 0 && (
            <ul>
              {rows.map((row) => (
                <li key={row.id}>
                  <div className="platform-structure-item-heading">
                    <strong>{row.title}</strong>
                    <span data-ready={row.status === "ready"}>
                      {row.status === "ready" ? "来源一致" : "保留原结构"}
                    </span>
                  </div>
                  <p>{row.reason}</p>
                  {row.before && row.after && (
                    <div className="platform-structure-diff">
                      <div>
                        <small>整理前</small>
                        <code>{row.before}</code>
                      </div>
                      <div>
                        <small>整理后</small>
                        <code>{row.after}</code>
                      </div>
                    </div>
                  )}
                  <div className="platform-structure-pages">
                    {row.pages.map((page) =>
                      onLocate ? (
                        <Button
                          key={page}
                          variant="outline"
                          size="sm"
                          onClick={() => onLocate(page)}
                          aria-label={`查看原件第 ${page} 页`}
                        >
                          原件第 {page} 页
                        </Button>
                      ) : (
                        <a
                          key={page}
                          href={`/api/v2/runs/${runId}/artifacts/original.pdf#page=${page}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          原件第 {page} 页 ↗
                        </a>
                      ),
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </details>
  );
}
