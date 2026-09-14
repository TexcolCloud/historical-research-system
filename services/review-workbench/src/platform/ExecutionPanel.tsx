import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExecutionCanvas } from "../components/execution/ExecutionCanvas";
import { Button } from "../components/ui/button";
import { client } from "./client";
import { executionGraph } from "./execution-graph";
import "../components/execution/agent-graph.css";
import RetryRunButton from "./RetryRunButton";
const states: Record<string, string> = {
  running: "执行中",
  completed: "已完成",
  failed: "未完成",
  waiting: "等待中",
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
  const id =
    selectedRun ||
    runs.data?.find((run) => run.kind === "cards")?.id ||
    runs.data?.[0]?.id;
  const activeRun = runs.data?.find((run) => run.id === id);
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
              {run.kind === "cards" ? "史料卡制作" : "书籍转换与入库"} ·{" "}
              {run.id.slice(-8)}
            </option>
          ))}
        </select>
      </label>
      {runs.isError && <p role="alert">{runs.error.message}</p>}
      {activeRun &&
        (activeRun.state === "failed" ||
          (activeRun.kind === "cards" &&
            activeRun.state === "needs_revision")) &&
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
  const records = useQuery({
    queryKey: ["platform", "executions", runId],
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/runs/{run_id}/executions", {
        params: { path: { run_id: runId } },
        signal,
      });
      if (!data) throw new Error("执行关系暂时无法读取。");
      return data;
    },
  });
  const graph = useMemo(
    () => executionGraph(records.data ?? []),
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
  const chosen = records.data?.find((row) => row.id === selected);
  return (
    <section className="execution-monitor">
      <div className="execution-heading">
        <h2>
          {graph.nodes.filter((node) => node.kind === "agent").length} 个 Agent
          · {graph.nodes.filter((node) => node.state === "running").length}{" "}
          项执行中
        </h2>
        <Button
          variant="outline"
          onClick={() => setRevision((value) => value + 1)}
        >
          整理布局
        </Button>
      </div>
      {records.isError && (
        <p role="alert">
          {records.error.message}
          <Button onClick={() => void records.refetch()}>重试</Button>
        </p>
      )}
      <div className="execution-body" data-detail={Boolean(chosen)}>
        <ExecutionCanvas
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
