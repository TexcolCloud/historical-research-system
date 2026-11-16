import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/client/api";
import { Button } from "@/components/ui/button";
import { sourceRoles, sourceLayers } from "./source-roles";

export default function SourceStructurePanel({
  runId,
  onLocate,
}: {
  runId: string;
  onLocate?: (page: number) => void;
}) {
  const [page, setPage] = useState(1);
  const cache = useQueryClient();
  const key = ["platform", "source-structure", runId, page];
  const query = useQuery({
    queryKey: key,
    queryFn: async ({ signal }) => {
      const { data } = await client.GET(
        "/api/v2/runs/{run_id}/source-structure",
        {
          params: { path: { run_id: runId }, query: { page } },
          signal,
        },
      );
      if (
        !data ||
        !Array.isArray(data.fragments) ||
        !Array.isArray(data.coverage)
      )
        throw new Error("暂时无法读取本页结构识别信息。");
      return data;
    },
    refetchInterval: (current) =>
      (current.state.data?.pending_structure_pages ?? 0) > 0 &&
      !current.state.data?.processing_paused
        ? 5000
        : false,
  });
  const recheck = useMutation({
    mutationFn: async (allMissing: boolean) => {
      if (!query.data) throw new Error("请先读取当前结构识别信息。");
      const { data, response } = await client.POST(
        "/api/v2/runs/{run_id}/source-structure/recheck",
        {
          params: { path: { run_id: runId } },
          body: {
            expected_snapshot_sha256: query.data.snapshot_sha256,
            force: !allMissing,
            ...(allMissing ? {} : { pages: [page] }),
          },
        },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "结构信息已更新或任务已暂停，请重新读取并检查任务状态。"
            : "重新核对结构尚未提交，请重试。",
        );
      return data;
    },
    onSuccess: () => {
      void cache.invalidateQueries({
        queryKey: ["platform", "source-structure", runId],
      });
    },
  });
  const data = query.data;
  const waiting = (data?.pending_structure_pages ?? 0) > 0;
  return (
    <section className="source-structure-inspector" aria-label="原页结构识别">
      <h3>原页结构识别</h3>
      <p>
        正文、注释和图表按来源保留。已核对的页眉、页脚和页码不参与默认检索；未识别或来源已变化的部分仍按原文保留。
      </p>
      {query.isError && (
        <p role="alert">
          {query.error.message}
          <Button onClick={() => void query.refetch()}>重新读取</Button>
        </p>
      )}
      {!data && query.isPending && <p role="status">正在读取本页结构…</p>}
      {data && (
        <>
          <div className="source-structure-controls">
            <label>
              查看原件页
              <select
                aria-label="结构识别原件页"
                value={page}
                onChange={(event) => {
                  setPage(Number(event.target.value));
                  recheck.reset();
                }}
              >
                {Array.from({ length: data.page_count }, (_, index) => (
                  <option key={index + 1} value={index + 1}>
                    {index + 1}
                  </option>
                ))}
              </select>
            </label>
            {onLocate ? (
              <Button variant="outline" onClick={() => onLocate(page)}>
                对照本页原图
              </Button>
            ) : (
              <a href={data.image} target="_blank" rel="noreferrer">
                对照本页原图
              </a>
            )}
            <small>
              {data.reviewed_pages} / {data.page_count} 页完成结构核对
            </small>
          </div>
          {data.processing_paused && waiting && (
            <p role="status">
              任务已暂停，已安排的结构核对保留，可在处理进展中恢复。
            </p>
          )}
          {waiting && !data.processing_paused && (
            <p role="status">
              还有 {data.pending_structure_pages} 页结构待核对，完成后自动更新。
            </p>
          )}
          {data.structure_task_state === "pending" && (
            <p role="alert">
              本页结构核对尚未完成，请在处理进展中查看原因并恢复。
            </p>
          )}
          <p>
            {data.status === "reviewed"
              ? "本页结构已核对"
              : data.status === "stale"
                ? "原文已变化，本页结构暂按原文保留"
                : "本页结构尚未核对，按原文保留"}
          </p>
          {!!data.fragments.length && (
            <table>
              <caption>识别出的来源片段</caption>
              <thead>
                <tr>
                  <th>内容类型</th>
                  <th>归属</th>
                  <th>原文片段</th>
                </tr>
              </thead>
              <tbody>
                {data.fragments.map((fragment) => (
                  <tr key={fragment.fragment_id}>
                    <td>{sourceRoles[fragment.role] ?? fragment.role}</td>
                    <td>{sourceLayers[fragment.layer] ?? fragment.layer}</td>
                    <td>
                      <span>
                        {fragment.text}
                        {fragment.text_truncated ? "…" : ""}
                      </span>
                      <small>
                        原文字符 {fragment.start}–{fragment.end}
                      </small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <details>
            <summary>查看全部识别覆盖情况</summary>
            <ul>
              {data.coverage.map((item) => (
                <li key={item.role}>
                  {sourceRoles[item.role] ?? item.role} ·{" "}
                  {item.status === "present"
                    ? "已识别"
                    : item.status === "absent"
                      ? "原图未见"
                      : "按原文保留"}
                  {item.reason ? `：${item.reason}` : ""}
                </li>
              ))}
            </ul>
          </details>
          <details>
            <summary>补充或重新核对结构</summary>
            <p>仅重新查看原图并更新结构识别，不改动已核对正文和人工修改。</p>
            <Button
              variant="outline"
              disabled={recheck.isPending || query.isFetching || waiting}
              onClick={() => recheck.mutate(false)}
            >
              重新核对本页结构
            </Button>
            {data.reviewed_pages < data.page_count && (
              <Button
                variant="outline"
                disabled={recheck.isPending || query.isFetching || waiting}
                onClick={() => recheck.mutate(true)}
              >
                补齐尚未核对的页面
              </Button>
            )}
          </details>
        </>
      )}
      {recheck.isError && (
        <p role="alert">
          {recheck.error.message}
          <Button onClick={() => void query.refetch()}>重新读取</Button>
        </p>
      )}
      {recheck.isSuccess && (
        <p role="status">
          {recheck.data.queued_pages.length
            ? `已安排 ${recheck.data.queued_pages.length} 页结构核对；原文和人工修改保留。`
            : "没有新增任务；页面已在处理中、已核对或仍需先完成内容核对。"}
        </p>
      )}
    </section>
  );
}
