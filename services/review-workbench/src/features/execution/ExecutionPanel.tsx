import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExecutionCanvas } from "@/components/execution/ExecutionCanvas.tsx";
import { Button } from "@/components/ui/button.tsx";
import { client } from "@/client/api.ts";
import type { components } from "@/client/schema.d.ts";
import { executionGraph } from "@/features/execution/execution-graph.ts";
import "@/components/execution/agent-graph.css";
import RetryRunButton from "@/features/runs/RetryRunButton.tsx";
const states: Record<string, string> = {
  running: "执行中",
  completed: "已完成",
  failed: "未完成",
  waiting: "等待中",
  paused: "已暂停",
  queued: "排队中",
  leased: "执行中",
  ready: "等待处理",
  pending: "待处理",
  superseded: "已由后续处理替代",
};
export default function ExecutionPanel({ bookId }: { bookId: string }) {
  const [selectedRun, setSelectedRun] = useState("");
  const runs = useQuery({
    queryKey: ["platform", "runs", bookId],
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/books/{book_id}/runs", {
        params: { path: { book_id: bookId } },
        signal,
      });
      if (!data) throw new Error("无法读取本书任务。");
      return data;
    },
  });
  const id = selectedRun || runs.data?.[0]?.id;
  const activeRun = runs.data?.find((run) => run.id === id);
  const waiting =
    activeRun?.state === "processing" &&
    ["model_transport_wait", "retrieval_wait", "vision_service_wait"].includes(
      String(activeRun.error?.code),
    );
  const recovery = activeRun?.error?.recovery;
  const retryAt =
    recovery && typeof recovery === "object" && "retry_at" in recovery
      ? recovery.retry_at
      : undefined;
  return (
    <section className="platform-executions">
      <label>
        本书任务{" "}
        <select
          aria-label="查看本书任务"
          value={id ?? ""}
          onChange={(e) => setSelectedRun(e.target.value)}
        >
          {runs.data?.map((run) => (
            <option key={run.id} value={run.id}>
              {run.kind === "cards"
                ? "历史史料卡任务（已停用）"
                : "书籍转换与入库"}{" "}
              · {run.id.slice(-8)}
            </option>
          ))}
        </select>
      </label>
      {runs.isError && <p role="alert">{runs.error.message}</p>}
      {waiting && (
        <p role="status">
          等待
          {activeRun?.error?.code === "retrieval_wait"
            ? "检索"
            : activeRun?.error?.code === "vision_service_wait"
              ? "本地视觉"
              : "文本"}
          服务恢复，已完成结果保留。
          {typeof retryAt === "number" &&
            Number.isFinite(retryAt) &&
            `预计 ${new Date(retryAt * 1000).toLocaleString("zh-CN")} 自动重试。`}
          无需重复点击，关闭页面不影响恢复。
        </p>
      )}
      {activeRun?.state === "failed" &&
        typeof activeRun.error?.message === "string" && (
          <p role="alert">{activeRun.error.message}</p>
        )}
      {activeRun &&
        activeRun.kind !== "cards" &&
        !activeRun.codex_document_id &&
        activeRun.state === "failed" &&
        id && (
          <RetryRunButton key={`${id}:${activeRun.revision}`} runId={id} />
        )}{" "}
      {id && <RunGraph key={id} runId={id} />}
    </section>
  );
}
function RunGraph({ runId }: { runId: string }) {
  const [selected, setSelected] = useState<string | null>(null),
    [revision, setRevision] = useState(0);
  const [history, setHistory] = useState(false),
    [label, setLabel] = useState(""),
    [offset, setOffset] = useState(0);
  const records = useQuery<components["schemas"]["ExecutionWindow"]>({
    queryKey: ["platform", "executions", runId, history, label, offset],
    placeholderData: (previous) => previous,
    queryFn: async ({ signal }) => {
      const { data } = await client.GET(
        "/api/v2/runs/{run_id}/execution-window",
        {
          params: {
            path: { run_id: runId },
            query: { history, label: label || undefined, offset },
          },
          signal,
        },
      );
      if (!data) throw new Error("执行关系暂时无法读取。");
      return data;
    },
  });
  const graph = useMemo(
    () => executionGraph(records.data?.records ?? []),
    [records.data],
  );
  const detail = useQuery({
    queryKey: ["platform", "execution-detail", selected],
    enabled: Boolean(selected),
    queryFn: async ({ signal }) => {
      const { data } = await client.GET(
        "/api/v2/executions/{execution_id}/details",
        { params: { path: { execution_id: selected! } }, signal },
      );
      return data;
    },
  });
  const chosen = records.data?.records?.find((row) => row.id === selected);
  return (
    <section className="execution-monitor">
      <div className="execution-heading">
        <h2>
          累计 {records.data?.total ?? 0} 条处理记录 · 当前{" "}
          {records.data?.stages
            ?.filter((stage) => ["running", "leased"].includes(stage.state))
            .reduce((sum, stage) => sum + stage.count, 0) ?? 0}{" "}
          项执行中
        </h2>
        <Button
          variant="outline"
          onClick={() => setRevision((value) => value + 1)}
        >
          整理布局
        </Button>
      </div>
      <div className="execution-stage-overview" aria-label="各阶段累计记录">
        {Array.from(
          new Set(records.data?.stages?.map((stage) => stage.label) ?? []),
        ).map((stage) => (
          <Button
            key={stage}
            variant={label === stage ? "secondary" : "outline"}
            onClick={() => {
              setLabel(stage);
              setHistory(true);
              setOffset(0);
              setSelected(null);
            }}
          >
            {stage} ·{" "}
            {records.data?.stages
              ?.filter((item) => item.label === stage)
              .reduce((sum, item) => sum + item.count, 0)}
          </Button>
        ))}
      </div>
      <div className="execution-heading">
        {records.isPlaceholderData ? (
          <p role="status">正在读取所选记录，暂显示上一批。</p>
        ) : (
          <p>
            {history ? "历史记录" : "当前与异常"}
            {label && ` · ${label}`} · 显示{" "}
            {graph.nodes.length ? offset + 1 : 0}–{offset + graph.nodes.length}{" "}
            / {records.data?.matching ?? 0} 条
          </p>
        )}
        <Button
          variant="outline"
          onClick={() => {
            setHistory(!history);
            setOffset(0);
            setLabel("");
            setSelected(null);
          }}
        >
          {history ? "查看当前与异常" : "查看历史记录"}
        </Button>
        <Button
          variant="outline"
          disabled={!offset || records.isFetching}
          onClick={() => {
            setOffset(Math.max(0, offset - 40));
            setSelected(null);
          }}
        >
          上一批
        </Button>
        <Button
          variant="outline"
          disabled={
            offset + 40 >= (records.data?.matching ?? 0) || records.isFetching
          }
          onClick={() => {
            setOffset(offset + 40);
            setSelected(null);
          }}
        >
          下一批
        </Button>
      </div>
      {!records.isPending && !graph.nodes.length && !records.isError && (
        <p>此范围没有处理记录。历史记录可按阶段查看。</p>
      )}
      {records.isError && (
        <p role="alert">
          {records.error.message}
          <Button onClick={() => void records.refetch()}>重新读取记录</Button>
        </p>
      )}
      <div className="execution-body" data-detail={Boolean(chosen)}>
        <ExecutionCanvas
          key={`${history}:${label}:${offset}:${records.isPlaceholderData}`}
          {...graph}
          selected={selected}
          revision={revision}
          renderNode={(node) => (
            <button
              className="execution-node-main"
              onClick={() => setSelected(node.id)}
              aria-pressed={node.id === selected}
            >
              <span className="execution-node-top">
                <small>
                  {node.kind === "agent"
                    ? "Agent"
                    : node.kind === "tool"
                      ? "工具"
                      : node.kind === "group"
                        ? "研究分工"
                        : "处理组件"}
                </small>
                <em>{states[node.state] ?? node.state}</em>
              </span>
              <strong>{node.label}</strong>
              <span className="execution-node-objective">
                {String(node.assignment?.objective ?? "")}
              </span>
            </button>
          )}
        />
        {chosen && (
          <aside className="execution-detail">
            <Button variant="ghost" onClick={() => setSelected(null)}>
              关闭详情
            </Button>
            <h3>{chosen.label}</h3>
            <p>{chosen.objective}</p>
            <p>
              {states[chosen.state] ?? chosen.state} ·{" "}
              {new Date(chosen.started_at).toLocaleString("zh-CN")}
            </p>
            <details>
              <summary>实际调用与结果</summary>
              <pre>{JSON.stringify(detail.data, null, 2)}</pre>
            </details>
          </aside>
        )}
      </div>
    </section>
  );
}
