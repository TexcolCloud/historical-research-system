import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, LoaderCircle, Circle } from "lucide-react";
import { client, type Book } from "@/client/api.ts";
import RecoveryPanel from "@/features/runs/RecoveryPanel.tsx";
import RetryRunButton from "@/features/runs/RetryRunButton.tsx";
import StructurePanel from "@/features/library/StructurePanel.tsx";
import { Button } from "@/components/ui/button.tsx";
import { bookWorkspaceHref } from "@/features/books/workspace.ts";

const phases = [
  { title: "接收原件", description: "文件上传与完整性校验" },
  { title: "识别正文", description: "OCR 识别正文，保存原件和可恢复结果" },
  { title: "原件视觉核验", description: "对照原书逐页核验已识别的内容" },
  { title: "人工核对", description: "处理机器发现的错误与不确定项" },
  { title: "整理与入库", description: "组装整书章节、建立出处与检索索引" },
];
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
      void query.invalidateQueries({ queryKey: ["platform", "runs", book.id] });
      void query.invalidateQueries({ queryKey: ["platform", "book", book.id] });
    },
  });
  const runs = useQuery({
    queryKey: ["platform", "runs", book.id],
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
        <h2>
          {paused
            ? "自动处理已暂停"
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
        {run?.codex_document_id &&
          !done &&
          (failed ||
            queued ||
            ["checking", "waiting", "blocked"].includes(
              run.recovery?.state ?? "",
            )) && <RecoveryPanel run={run} />}
        {run?.codex_document_id && (
          <p>Codex 自动处理 · Terra 识别 → Sol 原图审核 → 文章组织与入库</p>
        )}
        {pageProgress && (
          <p>识别与核验按页并行进行，各自完成全部页面后才显示“已完成”。</p>
        )}
        {run?.content_acceptance === "workflow_test" && (
          <p role="note">本材料用于流程测试；授权放行不代表内容准确率验收。</p>
        )}
      </div>
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
                  book.state !== "awaiting_review" &&
                  !needsHuman ? (
                  <LoaderCircle size={20} className="platform-spin" />
                ) : (
                  <Circle size={16} />
                )}
              </span>
              <div>
                <h3>
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
                </h3>
                <p>{phase.description}</p>
                {pageProgress && (index === 1 || index === 2) && (
                  <div
                    aria-label={index === 1 ? "逐页识别进度" : "逐页核验进度"}
                  >
                    <progress
                      value={
                        index === 1
                          ? pageProgress.extracted
                          : pageProgress.reviewed
                      }
                      max={pageProgress.total}
                    />
                    <p>
                      {index === 1 ? "已识别" : "已核验"}{" "}
                      {index === 1
                        ? pageProgress.extracted
                        : pageProgress.reviewed}{" "}
                      / {pageProgress.total} 页
                    </p>
                    {index === 2 && (
                      <p>
                        其中已通过 {pageProgress.approved} 页，待人工处理{" "}
                        {pageProgress.pending} 页。
                      </p>
                    )}
                  </div>
                )}
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
                      <p>初轮结束后继续汇总与复核；已处理不代表内容已通过。</p>
                    </div>
                  )}
              </div>
            </li>
          );
        })}
      </ol>
      {book.run_id && (
        <StructurePanel runId={book.run_id} revision={book.revision} />
      )}
      {runs.isError && <p role="alert">{runs.error.message}</p>}
      {((book.pending_count ?? 0) > 0 ||
        (book.navigation_pending_count ?? 0) > 0) && (
        <div className="workspace-next-action">
          <strong>
            {book.pending_count ?? 0} 页正文待核对 ·{" "}
            {book.navigation_pending_count ?? 0} 项目录待确认
          </strong>
          <a
            className="workspace-primary-link"
            href={`#${bookWorkspaceHref(book)}`}
          >
            开始人工核对 →
          </a>
          <p>你可以先完成待办，其他页面按当前运行状态继续处理。</p>
        </div>
      )}
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
      {control.isError && <p role="alert">{control.error.message}</p>}
      {pageProgress && pageProgress.pending > 0 && (
        <p>
          {pageProgress.pending} 页待你核对。
          {paused
            ? "恢复后，系统继续其余页面。"
            : run?.execution?.automatic_work_remaining
              ? failed
                ? "其余页面的自动处理已停止，恢复后继续；可先核对已生成的待办。"
                : "其余页面仍在自动处理，可同时核对。"
              : "自动处理已结束，确认剩余内容后继续整理入库。"}
        </p>
      )}
      {run?.execution && !done && !recoveryBusy && (
        <p role="status">
          {run.execution.coordinator_active
            ? "本书协调器正在执行。"
            : run.execution.state === "retryable_error"
              ? run.execution.technical_failures >= run.execution.retry_limit
                ? `本书自动处理已停止：连续 ${run.execution.technical_failures} 次技术失败。已完成结果保留，可从断点恢复。`
                : `本书遇到技术故障，正在等待重试（${run.execution.technical_failures}/${run.execution.retry_limit}）。`
              : paused
                ? "本书已暂停。"
                : "本书当前没有活动协调器。"}
          {run.execution.retry_at && run.execution.retry_at > Date.now() / 1000
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
          {run.scheduling.cooldown_until > Date.now() / 1000 && (
            <p>服务限流，正在冷却。已完成结果保留。</p>
          )}
          <table>
            <thead>
              <tr>
                <th>阶段</th>
                <th>运行 / 建议</th>
                <th>排队</th>
                <th>单页中位耗时</th>
                <th>近 5 分钟速度</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(run.scheduling.metrics).map(([stage, m]) => (
                <tr key={stage}>
                  <td>
                    {(
                      {
                        ocr: "识别",
                        review: "核验",
                        verify: "修正复核",
                        navigation: "目录关联",
                        organization: "文章整理",
                      } as Record<string, string>
                    )[stage] ?? stage}
                  </td>
                  <td>
                    {m.active} / {run.scheduling?.workers[stage]}
                  </td>
                  <td>{m.queued}</td>
                  <td>
                    {m.median_seconds == null
                      ? "采样中"
                      : `${m.median_seconds} 秒`}
                  </td>
                  <td>{m.pages_per_minute} 项/分</td>
                </tr>
              ))}
            </tbody>
          </table>
          {run.execution?.active_tasks?.map((t, i) => (
            <p key={i}>
              {t.stage} · {t.page ? `原件第 ${t.page} 页` : "整书任务"}
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
