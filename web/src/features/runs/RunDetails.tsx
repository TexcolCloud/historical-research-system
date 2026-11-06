import { queryKeys } from "@/client/query-keys.ts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, LoaderCircle, Circle } from "lucide-react";
import { client, type Book } from "@/client/api.ts";
import RecoveryPanel from "@/features/runs/RecoveryPanel.tsx";
import RetryRunButton from "@/features/runs/RetryRunButton.tsx";
import RuntimeControlNotice, {
  runtimeControlTitles,
} from "./RuntimeControlNotice";
import StructurePanel from "@/features/library/StructurePanel.tsx";
import { Button } from "@/components/ui/button.tsx";
import { bookWorkspaceHref } from "@/features/books/workspace.ts";

import { phases, stageLabels } from "./phase-display.ts";
const timingLabels: Record<string, string> = {
  queue_seconds: "队列等待",
  startup_seconds: "代理启动",
  original_reading_seconds: "原页阅读",
  candidate_comparison_seconds: "候选核对",
  submission_seconds: "提交保存",
};
export default function RunDetails({ book }: { book: Book }) {
  const query = useQueryClient();
  const control = useMutation({
    mutationFn: async (action: "pause" | "resume") => {
      const path =
        action === "pause"
          ? "/api/v2/runs/{run_id}/pause"
          : "/api/v2/runs/{run_id}/resume";
      const { data } = await client.POST(path, {
        params: { path: { run_id: book.run_id! } },
      });
      if (!data) throw new Error("操作尚未完成，请重试。已有结果已保留。");
      return data;
    },
    onSuccess: () => {
      void query.invalidateQueries({ queryKey: queryKeys.runs(book.id) });
      void query.invalidateQueries({ queryKey: queryKeys.book(book.id) });
    },
  });
  const runs = useQuery({
    queryKey: queryKeys.runs(book.id),
    enabled: Boolean(book.run_id),
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/books/{book_id}/runs", {
        params: { path: { book_id: book.id } },
        signal,
      });
      if (!data) throw new Error("暂时无法读取处理进展。");
      return data;
    },
  });
  const run = runs.data?.find((row) => row.id === book.run_id);
  const done = run?.state === "completed" || (!run && book.state === "ready");
  const failed = run?.state === "failed";
  const paused = run?.execution?.paused || run?.state === "paused";
  const queued = run?.state === "queued";
  const pageProgress = run?.page_progress;
  const recoveryBusy = ["checking", "waiting"].includes(
    run?.recovery?.state ?? "",
  );
  const runtimeControl =
    !done && !paused && run?.execution?.automatic_work_remaining !== false
      ? run?.scheduling?.runtime_control
      : undefined;
  const runtimeWaiting = ["cooldown", "probing", "blocked"].includes(
    runtimeControl?.state ?? "",
  );
  const runtimeTitle =
    runtimeControl && runtimeControl.state !== "normal"
      ? runtimeControlTitles[runtimeControl.state]
      : undefined;
  const showRecovery = Boolean(
    run?.codex_document_id &&
    !done &&
    (failed ||
      queued ||
      runtimeControl?.state === "blocked" ||
      ["checking", "waiting", "blocked"].includes(run.recovery?.state ?? "")),
  );
  const active = ["organization", "indexing", "ingestion", "complete"].includes(
    book.stage ?? "",
  )
    ? 4
    : book.stage === "review"
      ? 3
      : book.stage === "vision"
        ? 2
        : ["conversion", "ocr"].includes(book.stage ?? "")
          ? 1
          : 0;
  const pageStatuses = pageProgress
    ? [
        "done",
        pageProgress.extracted === pageProgress.total ? "done" : "current",
        pageProgress.reviewed === pageProgress.total
          ? "done"
          : pageProgress.extracted > 0
            ? "current"
            : "waiting",
        pageProgress.approved === pageProgress.total
          ? "done"
          : pageProgress.pending > 0
            ? "current"
            : "waiting",
        active >= 4 ? "current" : "waiting",
      ]
    : null;
  if (book.run_id && runs.isPending)
    return (
      <section className="platform-process">
        <p role="status">正在读取处理进展…</p>
      </section>
    );
  return (
    <section className="platform-process">
      <div className="platform-process-heading">
        <div className="platform-process-summary">
          <div>
            <h2>
              {paused
                ? "自动处理已暂停"
                : runtimeTitle
                  ? runtimeTitle
                  : run?.state === "queued"
                    ? "已排队，等待处理"
                    : done
                      ? "本书处理完成"
                      : failed
                        ? "处理需要恢复"
                        : book.state === "awaiting_review"
                          ? "有内容需要你核对"
                          : (book.pending_count ?? 0) > 0 ||
                              (book.navigation_pending_count ?? 0) > 0
                            ? "有内容需要你确认"
                            : "书籍正在处理中"}
            </h2>
            <p>已完成结果会保留，关闭页面不影响后台处理。</p>
          </div>
          {run &&
            !done &&
            !(failed && run.codex_document_id) &&
            !recoveryBusy &&
            (run.codex_document_id || paused) && (
              <Button
                variant="outline"
                disabled={control.isPending}
                onClick={() => control.mutate(paused ? "resume" : "pause")}
              >
                {control.isPending
                  ? "正在保存…"
                  : paused
                    ? "继续处理"
                    : "暂停自动处理"}
              </Button>
            )}
        </div>
        {control.isError && <p role="alert">{control.error.message}</p>}
        {showRecovery && run && <RecoveryPanel run={run} />}
        {!showRecovery && <RuntimeControlNotice control={runtimeControl} />}
        {run?.codex_document_id && (
          <details>
            <summary>查看自动处理方式</summary>
            <p>
              Codex 自动处理 · Terra 提取文字 → Sol 原图核对 → 文章整理与入库
            </p>
          </details>
        )}
        {pageProgress && (
          <p>
            文字提取与原图核对按页并行进行，各自完成全部页面后才显示“已完成”。
          </p>
        )}
        {run?.content_acceptance === "workflow_test" && (
          <p role="note">本材料用于流程测试；授权放行不代表内容准确率验收。</p>
        )}
      </div>
      {pageProgress && (
        <ol className="platform-page-progress" aria-label="并行页面进度">
          {[1, 2].map((index) => (
            <li key={index} data-status={done ? "done" : pageStatuses?.[index]}>
              <h3>{index === 1 ? "文字提取" : "原图核对"}</h3>
              <div aria-label={index === 1 ? "逐页识别进度" : "逐页核验进度"}>
                <p className="platform-progress-count" aria-hidden="true">
                  <strong>
                    {index === 1
                      ? pageProgress.extracted
                      : pageProgress.reviewed}
                  </strong>
                  <span>/ {pageProgress.total} 页</span>
                </p>
                <progress
                  value={
                    index === 1 ? pageProgress.extracted : pageProgress.reviewed
                  }
                  max={pageProgress.total || 1}
                />
                <p className="sr-only">
                  {index === 1
                    ? `已识别 ${pageProgress.extracted} / ${pageProgress.total} 页`
                    : `已核验 ${pageProgress.reviewed} / ${pageProgress.total} 页`}
                </p>
                {index === 1 ? (
                  <p>文字候选已保存，仍需对照原图核对。</p>
                ) : (
                  <p>
                    {pageProgress.machine_reviewed != null && (
                      <>其中机器核验 {pageProgress.machine_reviewed} 页；</>
                    )}
                    机器或人工最终通过 {pageProgress.approved} 页，待人工处理{" "}
                    {pageProgress.pending} 页。
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
      {((book.pending_count ?? 0) > 0 ||
        (book.navigation_pending_count ?? 0) > 0) && (
        <div className="workspace-next-action">
          <div>
            <strong>
              {book.pending_count ?? 0} 页正文待核对 ·{" "}
              {book.navigation_pending_count ?? 0} 项目录待确认
            </strong>
            <p>你可以先完成待办，其他页面按当前运行状态继续处理。</p>
          </div>
          <a
            className="workspace-primary-link"
            href={`#${bookWorkspaceHref(book)}`}
          >
            开始人工核对 →
          </a>
        </div>
      )}
      <details className="platform-stage-details">
        <summary>查看完整处理阶段</summary>
        <ol className="platform-process-steps">
          {phases.map((phase, index) => {
            const status = done
              ? "done"
              : (pageStatuses?.[index] ??
                (run?.codex_document_id && index === 1 && active < 4
                  ? "current"
                  : index < active
                    ? "done"
                    : index === active
                      ? "current"
                      : "waiting"));
            const needsHuman =
              status === "current" &&
              index === 3 &&
              (book.state === "awaiting_review" ||
                Boolean(pageProgress?.pending));
            return (
              <li key={phase.title} data-status={status}>
                <span className="platform-process-icon">
                  {status === "done" ? (
                    <Check size={20} />
                  ) : status === "current" &&
                    !failed &&
                    !paused &&
                    !queued &&
                    !runtimeWaiting &&
                    book.state !== "awaiting_review" &&
                    !needsHuman ? (
                    <LoaderCircle size={20} className="platform-spin" />
                  ) : (
                    <Circle size={16} />
                  )}
                </span>
                <div>
                  <strong className="platform-stage-title">
                    {phase.title}
                    <small>
                      {status === "done"
                        ? "已完成"
                        : status === "waiting"
                          ? "等待前序完成"
                          : failed
                            ? "需要恢复"
                            : queued
                              ? "排队中"
                              : paused
                                ? "已暂停"
                                : needsHuman
                                  ? "等待核对"
                                  : "正在处理"}
                    </small>
                  </strong>
                  <p>{phase.description}</p>
                  {!pageProgress &&
                    status === "current" &&
                    index === 2 &&
                    run?.progress && (
                      <div aria-label="逐页核验进度">
                        <progress
                          value={run.progress.completed}
                          max={run.progress.total}
                        />
                        <p>
                          初轮已核验 {run.progress.completed} /{" "}
                          {run.progress.total} 页
                        </p>
                        <p>
                          初轮结束后继续汇总与复核；已处理不代表内容已通过。
                        </p>
                      </div>
                    )}
                </div>
              </li>
            );
          })}
        </ol>
      </details>
      {book.run_id && (
        <StructurePanel runId={book.run_id} revision={book.revision} />
      )}
      {runs.isError && <p role="alert">{runs.error.message}</p>}
      {pageProgress && pageProgress.pending > 0 && (
        <p>
          {pageProgress.pending} 页待你核对。
          {paused
            ? "恢复后，系统继续其余页面。"
            : runtimeWaiting
              ? "服务恢复状态见上方提示，可先核对已生成的待办。"
              : run?.execution?.automatic_work_remaining
                ? failed
                  ? "其余页面的自动处理已停止，恢复后继续；可先核对已生成的待办。"
                  : "其余页面仍在自动处理，可同时核对。"
                : "自动处理已结束，确认剩余内容后继续整理入库。"}
        </p>
      )}
      {run?.execution &&
        !done &&
        !recoveryBusy &&
        (!runtimeControl || runtimeControl.state === "normal") &&
        !(failed && run.codex_document_id) && (
          <p role="status">
            {run.execution.coordinator_active
              ? "本书正在自动处理。"
              : run.execution.state === "retryable_error"
                ? run.execution.technical_failures >= run.execution.retry_limit
                  ? `本书自动处理已停止：连续 ${run.execution.technical_failures} 次技术失败。已完成结果保留，可从断点恢复。`
                  : `本书遇到技术故障，正在等待重试（${run.execution.technical_failures}/${run.execution.retry_limit}）。`
                : paused
                  ? "本书已暂停。"
                  : "本书当前没有正在执行的自动任务。"}
            {run.execution.retry_at &&
            run.execution.retry_at > Date.now() / 1000
              ? `预计 ${new Date(run.execution.retry_at * 1000).toLocaleTimeString()} 后重试。`
              : ""}
          </p>
        )}
      {run?.execution?.last_progress_at && (
        <p>
          最近完成：
          {new Date(run.execution.last_progress_at * 1000).toLocaleTimeString()}
        </p>
      )}
      {run?.scheduling && (
        <details>
          <summary>处理速度与调度</summary>
          <p>
            最多 {run.scheduling.capacity} 个工作代理，每批最多{" "}
            {run.scheduling.batch_size} 页；完成当前页后再调整分配。
          </p>
          {run.scheduling.runtime_counts && (
            <p>
              正在启动 {run.scheduling.runtime_counts.starting ?? 0}， 正在处理{" "}
              {run.scheduling.runtime_counts.busy ?? 0}， 等待任务{" "}
              {run.scheduling.runtime_counts.idle ?? 0}； 工作区已占用{" "}
              {run.scheduling.workspace_occupied ?? 0} 个名额（含启动中）。
            </p>
          )}
          {!runtimeControl &&
            run.scheduling.cooldown_until > Date.now() / 1000 && (
              <p>服务暂时限制请求，系统稍后自动重试。</p>
            )}
          <table>
            <thead>
              <tr>
                <th>阶段</th>
                <th>运行 / 建议</th>
                <th>排队</th>
                <th>单页中位耗时</th>
                <th>分段中位耗时</th>
                <th>实际采样速度</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(run.scheduling.metrics).map(([stage, m]) => (
                <tr key={stage}>
                  <td>{stageLabels[stage] ?? stage}</td>
                  <td>
                    {m.active} / {run.scheduling?.workers[stage]}
                  </td>
                  <td>{m.queued}</td>
                  <td>
                    {m.median_seconds == null
                      ? "采样中"
                      : `${m.median_seconds} 秒`}
                  </td>
                  <td>
                    {Object.entries(m.phase_medians ?? {}).length
                      ? Object.entries(m.phase_medians ?? {}).map(
                          ([phase, seconds]) => (
                            <div key={phase}>
                              {timingLabels[phase] ?? phase}：{seconds} 秒
                            </div>
                          ),
                        )
                      : "暂无分段样本"}
                  </td>
                  <td>
                    {m.samples ? `${m.pages_per_minute} 项/分` : "采样中"}
                    {!!m.sample_window_seconds && (
                      <small>（{m.sample_window_seconds} 秒窗口）</small>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {run.execution?.active_tasks?.map((t, i) => (
            <p key={i}>
              {stageLabels[t.stage] ?? "其他处理"} ·{" "}
              {t.page ? `原件第 ${t.page} 页` : "整书任务"}
              {t.started_at
                ? ` · 开始于 ${new Date(t.started_at * 1000).toLocaleTimeString()}`
                : ""}
            </p>
          ))}
        </details>
      )}
      {failed && run && !run.codex_document_id && (
        <RetryRunButton key={`${run.id}:${run.revision}`} runId={run.id} />
      )}
    </section>
  );
}
