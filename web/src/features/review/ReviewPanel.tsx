import { queryKeys } from "@/client/query-keys.ts";
import { useEffect, useState } from "react";
import IssueEditor from "./IssueEditor.tsx";
import { useRuntimeScope } from "@/app/runtime-scope.ts";
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
    queryKey: queryKeys.issue(id),
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
  requestedPage,
}: {
  runId: string;
  stage?: string;
  state?: string;
  bookId?: string;
  requestedPage?: number;
}) {
  const query = useQueryClient();
  const runtimeScope = useRuntimeScope();
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
    : (issues.data?.find((issue) => issue.page === requestedPage)?.id ??
      issues.data?.[0]?.id ??
      "");
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
        ? `本页已提交，剩余 ${receipt.pending_count} 项待核对。可继续处理下一项。`
        : "本页已提交，当前核对清单已处理完。整书核对通过后，系统会继续整理入库。",
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
          <Button onClick={() => void issues.refetch()}>
            重新读取核对清单
          </Button>
        </p>
      ) : issues.isPending ? (
        <p role="status">正在读取核对清单…</p>
      ) : !issues.data.length ? (
        <div className="platform-empty">
          <h2>
            {preparing
              ? state === "failed"
                ? "识别或核验尚未完成"
                : state === "paused"
                  ? "自动处理已暂停"
                  : "文字提取与原图核对进行中"
              : "当前没有待核对内容"}
          </h2>
          <p>
            {preparing
              ? state === "failed"
                ? "请前往处理进展，点击“从断点恢复”。尚未生成的核对项目将在处理后列出。"
                : state === "paused"
                  ? "请前往处理进展，点击“继续处理”。恢复后发现的疑点会列在这里。"
                  : "暂未发现需要你处理的疑点。新疑点会自动列出，可先阅读已有底稿。"
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
              <Button onClick={() => void current.refetch()}>
                重新读取本页
              </Button>
            </p>
          ) : current.data ? (
            <IssueEditor
              key={`${current.data.id}:${("review_input_sha256" in current.data && current.data.review_input_sha256) || current.data.text_sha256}`}
              issue={current.data}
              workflowTest={runtimeScope === "workflow_only"}
              runtimeScope={runtimeScope}
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
