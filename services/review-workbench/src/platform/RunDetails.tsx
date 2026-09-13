import { useQuery } from "@tanstack/react-query";
import { Check, LoaderCircle, Circle } from "lucide-react";
import { client, type Book } from "./client";
import RetryRunButton from "./RetryRunButton";

const phases = [
  { title: "接收原件", description: "文件上传与完整性校验" },
  { title: "识别正文", description: "OCR 识别正文，保存原件和可恢复结果" },
  { title: "原件视觉核验", description: "对照原书逐页核验已识别的内容" },
  { title: "人工核对", description: "处理机器发现的错误与不确定项" },
  { title: "整理与入库", description: "组装整书章节、建立出处与检索索引" },
  {
    title: "制作史料卡",
    description: "动态研究分工，文本和原件核验后自动采用",
  },
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
  const run = runs.data?.find((row) => row.id === book.run_id),
    cards = runs.data?.find((row) => row.kind === "cards");
  const active = cards
    ? 5
    : ["organization", "indexing", "ingestion"].includes(book.stage ?? "")
      ? 4
      : book.stage === "review"
        ? 3
        : book.stage === "vision"
          ? 2
          : ["conversion", "ocr"].includes(book.stage ?? "")
            ? 1
            : 0;
  const failed = run?.state === "failed" || cards?.state === "failed",
    done = cards?.state === "completed",
    needsRevision = cards?.state === "needs_revision";
  return (
    <section className="platform-process">
      <div className="platform-process-heading">
        <h2>
          {done
            ? "本书处理完成"
            : failed
              ? "处理需要恢复"
              : needsRevision
                ? "部分卡片未通过机器核验"
                : book.state === "awaiting_review"
                  ? "有内容需要你核对"
                  : "书籍正在处理中"}
        </h2>
        <p>已完成结果会保留，关闭页面不影响后台处理。</p>
      </div>
      <ol className="platform-process-steps">
        {phases.map((phase, index) => {
          const status =
            index < active || done
              ? "done"
              : index === active
                ? "current"
                : "waiting";
          return (
            <li key={phase.title} data-status={status}>
              <span className="platform-process-icon">
                {status === "done" ? (
                  <Check size={20} />
                ) : status === "current" &&
                  !failed &&
                  !needsRevision &&
                  book.state !== "awaiting_review" ? (
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
                          : book.state === "awaiting_review"
                            ? "等待核对"
                            : needsRevision
                              ? "机器核验未通过"
                              : "正在处理"}
                  </small>
                </h3>
                <p>{phase.description}</p>
                {status === "current" && index === 2 && run?.progress && (
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
                {status === "current" && book.state === "awaiting_review" && (
                  <a href={`#/books/${book.id}?tab=review`}>
                    开始核对
                    {run?.pending_count ? ` · ${run.pending_count} 项` : ""} →
                  </a>
                )}
                {status === "current" && cards && (
                  <a href={`#/books/${book.id}?tab=execution`}>
                    查看实际研究分工 →
                  </a>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {runs.isError && <p role="alert">{runs.error.message}</p>}
      {failed && (
        <RetryRunButton
          runId={cards?.state === "failed" ? cards.id : run!.id}
        />
      )}
    </section>
  );
}
