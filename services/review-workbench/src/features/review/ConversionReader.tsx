import { useEffect, useState } from "react";
import {
  keepPreviousData,
  queryOptions,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Button } from "@/components/ui/button.tsx";
import { RenderedMarkdown } from "@/editor/RenderedMarkdown.tsx";
import { client } from "@/client/api.ts";
import StructurePanel from "@/features/library/StructurePanel.tsx";

const pageOptions = (runId: string, page: number) =>
  queryOptions({
    queryKey: ["platform", "conversion-page", runId, page],
    queryFn: async ({ signal }) => {
      const { data, response } = await client.GET(
        "/api/v2/runs/{run_id}/review-pages/{page}",
        {
          signal,
          params: { path: { run_id: runId, page } },
        },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "识别底稿尚未保存，请等待 OCR 完成。"
            : "暂时无法读取底稿，请重试。",
        );
      return data;
    },
  });

export default function ConversionReader({ runId }: { runId: string }) {
  const [page, setPage] = useState(1);
  const query = useQueryClient();
  const result = useQuery({
    ...pageOptions(runId, page),
    placeholderData: keepPreviousData,
  });
  const data = result.data;
  useEffect(() => {
    if (data && data.page < data.page_count)
      void query.prefetchQuery(pageOptions(runId, data.page + 1));
  }, [data, query, runId]);
  return (
    <section className="platform-conversion-reader">
      <StructurePanel runId={runId} onLocate={setPage} />
      <p className="platform-inline-status">
        {data?.machine_status === "unreviewed-source"
          ? "此处为已保存的 OCR 底稿，尚未完成原件核验，仅供阅读。核验完成后，需人工确认的问题会出现在问题清单。"
          : "此处为转换时的识别底稿，包含未核对内容。人工修改与放行请返回问题清单；完成核对后生成最终阅读版。"}
      </p>
      {result.isError && (
        <p role="alert">
          {result.error.message}
          <Button onClick={() => void result.refetch()}>重试</Button>
        </p>
      )}
      {!data ? (
        <p role="status">
          {result.isPending ? "正在读取底稿…" : "暂无可显示的底稿。"}
        </p>
      ) : (
        <>
          <div className="platform-review-actions">
            <Button
              variant="outline"
              disabled={page <= 1 || result.isFetching}
              onClick={() => setPage(page - 1)}
            >
              上一页
            </Button>
            <label>
              原件页码{" "}
              <select
                aria-label="底稿原件页码"
                value={page}
                disabled={result.isFetching}
                onChange={(event) => setPage(Number(event.target.value))}
              >
                {Array.from({ length: data.page_count }, (_, index) => (
                  <option key={index + 1} value={index + 1}>
                    {index + 1} / {data.page_count}
                  </option>
                ))}
              </select>
            </label>
            <Button
              variant="outline"
              disabled={page >= data.page_count || result.isFetching}
              onClick={() => setPage(page + 1)}
            >
              下一页
            </Button>
            <span role="status">
              {result.isFetching
                ? "正在读取目标页…"
                : `当前原件第 ${data.page} 页`}
            </span>
          </div>
          <div
            className="platform-review-content"
            aria-busy={result.isFetching}
          >
            <article>
              <RenderedMarkdown
                markdown={data.text || "（本页没有可显示的识别文字）"}
                footnotes={data.footnotes}
                assetBaseUrl={`/api/v2/runs/${runId}/artifacts`}
              />
            </article>
            <aside>
              <figure>
                <a href={data.image} target="_blank" rel="noreferrer">
                  打开原图 · 第 {data.page} 页
                </a>
                <img src={data.image} alt={`底稿原件第 ${data.page} 页`} />
              </figure>
            </aside>
          </div>
        </>
      )}
    </section>
  );
}
