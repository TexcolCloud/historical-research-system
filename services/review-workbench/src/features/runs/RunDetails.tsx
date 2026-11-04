import { useQuery } from "@tanstack/react-query";
import { Check, LoaderCircle, Circle } from "lucide-react";
import { client, type Book } from "@/client/api.ts";
import RetryRunButton from "@/features/runs/RetryRunButton.tsx";
import StructurePanel from "@/features/library/StructurePanel.tsx";

const phases = [
  { title: "接收原件", description: "文件上传与完整性校验" },
  { title: "识别正文", description: "OCR 识别正文，保存原件和可恢复结果" },
  { title: "原件视觉核验", description: "对照原书逐页核验已识别的内容" },
  { title: "人工核对", description: "处理机器发现的错误与不确定项" },
  { title: "整理与入库", description: "组装整书章节、建立出处与检索索引" },
];
export default function RunDetails({ book }: { book: Book }) {
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
  const pageProgress = run?.page_progress;
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
  const pageStatuses = pageProgress ? [
    "done",
    pageProgress.extracted === pageProgress.total ? "done" : "current",
    pageProgress.reviewed === pageProgress.total ? "done" : pageProgress.extracted > 0 ? "current" : "waiting",
    pageProgress.approved === pageProgress.total ? "done" : pageProgress.pending > 0 ? "current" : "waiting",
    active >= 4 ? "current" : "waiting",
  ] : null;
  return (
    <section className="platform-process">
      <div className="platform-process-heading">
        <h2>
          {done
            ? "本书处理完成"
            : failed
              ? "处理需要恢复"
              : book.state === "awaiting_review"
                ? "有内容需要你核对"
                : "书籍正在处理中"}
        </h2>
        <p>已完成结果会保留，关闭页面不影响后台处理。</p>
        {run?.codex_document_id && <p>Codex 自动处理 · Terra 识别 → Sol 原图审核 → 文章组织与入库</p>}
        {pageProgress && <p>识别与核验按页并行进行，各自完成全部页面后才显示“已完成”。</p>}
        {run?.content_acceptance === "workflow_test" && <p role="note">本材料用于流程测试；授权放行不代表内容准确率验收。</p>}
      </div>
      <ol className="platform-process-steps">
        {phases.map((phase, index) => {
          const status =
            done
              ? "done"
              : pageStatuses?.[index] ?? (run?.codex_document_id && index === 1 && active < 4
                ? "current"
                : index < active ? "done" : index === active
                ? "current"
                : "waiting");
          const needsHuman = status === "current" && index === 3 &&
            (book.state === "awaiting_review" || Boolean(pageProgress?.pending));
          return (
            <li key={phase.title} data-status={status}>
              <span className="platform-process-icon">
                {status === "done" ? (
                  <Check size={20} />
                ) : status === "current" &&
                  !failed &&
                  book.state !== "awaiting_review" && !needsHuman ? (
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
                          : book.state === "awaiting_review" || needsHuman
                            ? "等待核对"
                            : "正在处理"}
                  </small>
                </h3>
                <p>{phase.description}</p>
                {pageProgress && (index === 1 || index === 2) && (
                  <div aria-label={index === 1 ? "逐页识别进度" : "逐页核验进度"}>
                    <progress value={index === 1 ? pageProgress.extracted : pageProgress.reviewed} max={pageProgress.total} />
                    <p>{index === 1 ? "已识别" : "已核验"} {index === 1 ? pageProgress.extracted : pageProgress.reviewed} / {pageProgress.total} 页</p>
                    {index === 2 && <p>其中已通过 {pageProgress.approved} 页，待人工处理 {pageProgress.pending} 页。</p>}
                  </div>
                )}
                {!pageProgress && status === "current" && index === 2 && run?.progress && (
                  <div aria-label="逐页核验进度">
                    <progress
                      value={run.progress.completed}
                      max={run.progress.total}
                    />
                    <p>
                      初轮已核验 {run.progress.completed} / {run.progress.total}{" "}
                      页
                    </p>
                    <p>初轮结束后继续汇总与复核；已处理不代表内容已通过。</p>
                  </div>
                )}
                {needsHuman && (
                  <a href={`#/books/${book.id}?tab=review`}>
                    开始核对
                    {run?.pending_count ? ` · ${run.pending_count} 项` : ""} →
                  </a>
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
      {failed && run && (
        <RetryRunButton key={`${run.id}:${run.revision}`} runId={run.id} />
      )}
    </section>
  );
}
