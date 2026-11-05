import { useQuery } from "@tanstack/react-query";
import { client } from "@/client/api.ts";
import type { components } from "@/client/schema.d.ts";
import RetryRunButton from "./RetryRunButton";

const stages: Record<string, string> = {
  ocr: "识别正文",
  review: "原页核验",
  verify: "修正复核",
  navigation: "目录关联",
  organization: "文章整理",
};
export default function RecoveryPanel({
  run,
}: {
  run: components["schemas"]["RunSummary"];
}) {
  const report = useQuery({
    queryKey: ["platform", "recovery", run.id],
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/runs/{run_id}/recovery", {
        params: { path: { run_id: run.id } },
        signal,
      });
      if (!data)
        throw new Error("暂时无法读取恢复条件。已保存结果仍保留，请重试读取。");
      return data;
    },
  });
  const data = report.data;
  const saved =
    run.state === "failed" && run.recovery?.state === "resumed"
      ? undefined
      : run.recovery;
  const status =
    saved?.state ?? (data?.state === "waiting" ? "available" : data?.state);
  const waiting = ["checking", "waiting", "retrying", "checkpoint"].includes(
    status ?? "",
  );
  const title =
    status === "retrying"
      ? "等待自动重试"
      : status === "checkpoint"
        ? "等待自动检查点恢复"
        : status === "checking"
          ? "正在检查恢复条件"
          : status === "waiting"
            ? "等待旧任务退出"
            : status === "blocked"
              ? "恢复检查未通过"
              : status === "resumed" && run.state === "queued"
                ? "已加入恢复队列"
                : data?.automatic_checkpoint_used
                  ? "自动恢复未成功"
                  : "自动处理已停止";
  return (
    <section
      className="workspace-next-action recovery-panel"
      aria-label="断点恢复"
    >
      <h3>{title}</h3>
      {report.isPending && <p role="status">正在读取检查点摘要…</p>}
      {report.isError && (
        <p role="alert">
          {report.error.message}
          <button onClick={() => void report.refetch()}>重新读取</button>
        </p>
      )}
      <p>{saved?.reason || data?.reason}</p>
      {data?.preserved_ocr != null && (
        <p>
          已保留：{data.preserved_ocr} 页识别，{data.preserved_approved ?? 0}{" "}
          页已通过；人工决定与草稿保留。
        </p>
      )}
      {data?.pending_human ? (
        <p>{data.pending_human} 页仍需你核对，技术恢复不会代替内容裁决。</p>
      ) : null}
      {data?.stage && (
        <p>
          继续位置（以恢复检查为准）：{stages[data.stage] ?? data.stage}
          {data.page ? ` · 原件第 ${data.page} 页` : ""}。
        </p>
      )}
      {waiting ? (
        <p role="status">
          {status === "checking"
            ? "恢复请求已保存，后台正在检查。"
            : status === "waiting"
              ? "旧任务退出后自动继续检查，无需重复点击。"
              : "系统按有限预算自动恢复，失败后才需要手动操作。"}
        </p>
      ) : (
        run.state === "failed" && (
          <RetryRunButton
            key={`${run.id}:${run.revision}`}
            runId={run.id}
            checkpoint
          />
        )
      )}
      <details>
        <summary>故障详情与恢复范围</summary>
        <p>
          先检查本机执行器、Codex
          登录和原件证据，再继续未完成任务。普通尝试最多三次，自动检查点回退最多一次；手动恢复建立新的有限重试批次。
        </p>
        <p>
          检查点文件损坏或丢失时，请恢复原件与工作区文件后再试。已通过页面、目录决定、草稿和脚注继续保留。
        </p>
        {typeof run.error?.detail === "string" && <p>{run.error.detail}</p>}
      </details>
    </section>
  );
}
