import { useEffect, useState } from "react";
import IssueEditor from "./IssueEditor.tsx";
import { useOutletContext } from "react-router-dom";
import type { WorkspaceContext } from "@/app/AppShell.tsx";
import {
  useIsMutating,
  useQuery,
  useQueryClient,
  queryOptions,
} from "@tanstack/react-query";
import { Button } from "@/components/ui/button.tsx";
import { client, readReviewIssues } from "@/client/api.ts";
import {
  applyReviewReceipt,
  issuesKey,
} from "@/features/review/review-cache.ts";
import type { components } from "@/client/schema.d.ts";
import ConversionReader from "@/features/review/ConversionReader.tsx";

const issueOptions = (id: string) =>
  queryOptions({
    queryKey: ["platform", "issue", id],
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/reviews/{issue_id}", {
        params: { path: { issue_id: id } },
        signal,
      });
      if (!data) throw new Error("无法读取当前问题，请重试。");
      return data;
    },
  });

export default function ReviewPanel({
  runId,
  stage,
  state,
  bookId,
}: {
  runId: string;
  stage?: string;
  state?: string;
  bookId?: string;
}) {
  const query = useQueryClient();
  const workflowTest =
    useOutletContext<WorkspaceContext>()?.workflowTest ?? false;
  const busy =
    useIsMutating({ mutationKey: ["platform", "review-write", runId] }) > 0;
  const [selected, setSelected] = useState<string | null>(null),
    [message, setMessage] = useState("");
  const [showDraft, setShowDraft] = useState(false);
  const preparing = ["upload", "conversion", "ocr", "vision"].includes(
    stage ?? "",
  );
  const draftPreparing = ["upload", "conversion"].includes(stage ?? "");
  const issues = useQuery({
    queryKey: issuesKey(runId),
    queryFn: ({ signal }) => readReviewIssues(runId, signal),
  });
  const currentId = issues.data?.some((issue) => issue.id === selected)
    ? selected!
    : (issues.data?.[0]?.id ?? "");
  const current = useQuery({
    ...issueOptions(currentId),
    enabled: Boolean(currentId),
  });
  const nextId = issues.data?.find((issue) => issue.id !== currentId)?.id;
  useEffect(() => {
    if (nextId) void query.prefetchQuery(issueOptions(nextId));
  }, [nextId, query]);
  function saved(receipt: components["schemas"]["ReviewReceipt"]) {
    applyReviewReceipt(query, receipt);
    setSelected(receipt.next_issue_id);
    setMessage(
      receipt.pending_count
        ? `决定已保存，剩余 ${receipt.pending_count} 项。`
        : "当前待办已处理完成。系统继续剩余自动任务，整书核验通过后整理入库。",
    );
  }
  return (
    <section className="platform-review">
      {bookId ? (
        <div className="review-workspace-intro">
          <p>逐处完成保留或修改，全部处理后提交本页。草稿会自动保存。</p>
          <a href={`#/books/${bookId}?tab=reader`}>阅读已有底稿</a>
        </div>
      ) : (
        <Button
          variant="outline"
          disabled={busy || draftPreparing}
          onClick={() => setShowDraft(!showDraft)}
        >
          {draftPreparing
            ? "识别完成后可浏览底稿"
            : showDraft
              ? "返回问题清单"
              : "浏览全部识别底稿"}
        </Button>
      )}
      {message && (
        <p role="status" className="platform-inline-status">
          {message}
        </p>
      )}
      {showDraft ? (
        <ConversionReader runId={runId} />
      ) : issues.isError ? (
        <p role="alert">
          {issues.error.message}
          <Button onClick={() => void issues.refetch()}>重试</Button>
        </p>
      ) : issues.isPending ? (
        <p role="status">正在读取核对清单…</p>
      ) : !issues.data.length ? (
        <div className="platform-empty">
          <h2>
            {preparing
              ? state === "failed"
                ? "识别或核验尚未完成"
                : "识别与原件核验进行中"
              : "当前没有待核对内容"}
          </h2>
          <p>
            {preparing
              ? state === "failed"
                ? "请在处理进展中恢复本阶段。尚未形成完整核对清单，不代表内容已经通过。"
                : "核验完成后，需要你确认的问题会列在这里。当前清单为空不代表内容已经通过，可在处理进展中查看核验页数。"
              : "已完成核对的书籍会自动继续整理章节并入库。"}
          </p>
          {bookId && (
            <a href={`#/books/${bookId}?tab=progress`}>查看后续处理进展 →</a>
          )}
        </div>
      ) : (
        <div className="platform-review-grid">
          <nav aria-label="问题清单">
            {issues.data.map((issue) => (
              <button
                key={issue.id}
                disabled={busy}
                aria-current={issue.id === currentId ? "true" : undefined}
                onPointerEnter={() =>
                  void query.prefetchQuery(issueOptions(issue.id))
                }
                onClick={() => setSelected(issue.id)}
              >
                原件第 {issue.page} 页
              </button>
            ))}
          </nav>
          {current.isError ? (
            <p role="alert">
              {current.error.message}
              <Button onClick={() => void current.refetch()}>重试</Button>
            </p>
          ) : current.data ? (
            <IssueEditor
              key={current.data.id}
              issue={current.data}
              workflowTest={workflowTest}
              onSaved={saved}
            />
          ) : (
            <p role="status">正在读取本项正文和原件…</p>
          )}
        </div>
      )}
    </section>
  );
}
