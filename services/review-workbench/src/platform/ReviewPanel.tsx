import { useEffect, useRef, useState } from "react";
import {
  useMutation,
  useIsMutating,
  useQuery,
  useQueryClient,
  queryOptions,
} from "@tanstack/react-query";
import { Button } from "../components/ui/button";
import { RenderedMarkdown } from "../editor/RenderedMarkdown";
import { MarkdownEditor } from "../editor/LazyMarkdownEditor";
import { client, readReviewIssues } from "./client";
import { applyReviewReceipt, issuesKey } from "./review-cache";
import type { components } from "./schema";
import ConversionReader from "./ConversionReader";

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
}: {
  runId: string;
  stage?: string;
  state?: string;
}) {
  const query = useQueryClient();
  const busy =
    useIsMutating({ mutationKey: ["platform", "review-write", runId] }) > 0;
  const [selected, setSelected] = useState<string | null>(null),
    [message, setMessage] = useState("");
  const [showDraft, setShowDraft] = useState(false);
  const preparing = ["upload", "conversion", "ocr", "vision"].includes(
    stage ?? "",
  );
  const draftPreparing = ["upload", "conversion", "ocr"].includes(stage ?? "");
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
        : "本书核对已完成，系统将继续整理章节并入库。",
    );
  }
  return (
    <section className="platform-review">
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
      <p role="status" className="platform-inline-status">
        {message ||
          "对照原件逐项核对。需要处理的问题全部完成后，本书才会入库。"}
      </p>
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
                原件第 {issue.page} 页<span>待人工核对</span>
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
              key={`${current.data.id}:${current.data.revision}`}
              issue={current.data}
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

function IssueEditor({
  issue,
  onSaved,
}: {
  issue: components["schemas"]["ReviewIssue"];
  onSaved: (receipt: components["schemas"]["ReviewReceipt"]) => void;
}) {
  const query = useQueryClient(),
    draftKey = ["platform", "review-edit", issue.id, issue.revision];
  const [editing, setEditing] = useState(Boolean(issue.draft_text)),
    [text, setText] = useState(
      query.getQueryData<string>(draftKey) ?? issue.draft_text ?? issue.text,
    );
  const [revision, setRevision] = useState(issue.revision);
  const draft = useMutation({
    mutationKey: ["platform", "review-write", issue.run_id],
    mutationFn: async () => {
      const { data, response } = await client.PUT(
        "/api/v2/reviews/{issue_id}/draft",
        {
          params: { path: { issue_id: issue.id } },
          body: { text, expected_revision: revision },
        },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "草稿版本已变化，请重新读取。"
            : "草稿暂未保存，请重试。",
        );
      return data;
    },
    onSuccess: (data) => {
      setRevision(data.revision);
      query.setQueryData(issueOptions(issue.id).queryKey, {
        ...issue,
        revision: data.revision,
        draft_text: data.draft_text,
      });
    },
  });
  const request = useRef<components["schemas"]["ReviewDecision"] | null>(null);
  const changed = text !== issue.text;
  const mutation = useMutation({
    mutationKey: ["platform", "review-write", issue.run_id],
    mutationFn: async () => {
      const action = changed ? "correct" : "confirm";
      if (
        !request.current ||
        request.current.expected_revision !== revision ||
        request.current.action !== action ||
        request.current.text !== (changed ? text : undefined)
      )
        request.current = {
          decision_id: crypto.randomUUID(),
          expected_revision: revision,
          expected_text_sha256: issue.text_sha256,
          action,
          ...(changed ? { text } : {}),
        };
      const { data, response } = await client.POST(
        "/api/v2/reviews/{issue_id}/decisions",
        { params: { path: { issue_id: issue.id } }, body: request.current },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "内容已被处理或版本发生变化，请重新读取当前问题。"
            : "决定尚未确认保存，请重试；当前修改已保留。",
        );
      return data;
    },
    onSuccess: onSaved,
  });
  return (
    <div className="platform-review-content">
      <article>
        <div className="platform-review-actions">
          <strong>原件第 {issue.pages.join("、")} 页</strong>
          <Button
            disabled={
              mutation.isPending || draft.isPending || issue.state !== "pending"
            }
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending
              ? "正在保存…"
              : changed
                ? "保存修改并确认"
                : "确认无误并放行"}
          </Button>
          <Button
            variant="outline"
            disabled={mutation.isPending || draft.isPending}
            onClick={() => setEditing(!editing)}
          >
            {editing ? "返回阅读" : "编辑正文"}
          </Button>
          {editing && (
            <Button
              variant="outline"
              disabled={mutation.isPending || draft.isPending}
              onClick={() => draft.mutate()}
            >
              {draft.isPending ? "正在存草稿…" : "保存草稿"}
            </Button>
          )}
        </div>
        {mutation.isError && <p role="alert">{mutation.error.message}</p>}
        {draft.isError && <p role="alert">{draft.error.message}</p>}
        {draft.isSuccess && <p role="status">草稿已保存，尚未确认放行。</p>}
        <ul className="platform-review-reasons">
          {issue.reasons.map((reason) => (
            <li key={reason}>{reviewReason(reason)}</li>
          ))}
        </ul>
        {issue.kind === "figure" && (
          <p className="platform-inline-status">
            本项为插图或地图，请核对右侧原图及下方图说。图内标签保留在图片中；阅读视图隐藏了导出占位标记，编辑时仍保留完整底稿。
          </p>
        )}
        {issue.kind === "page" && (
          <p className="platform-inline-status">
            本项按整页展示，因为尚未完成核验或无法可靠定位到具体段落；并不表示页面中的每一段都有错误。
          </p>
        )}
        <div inert={mutation.isPending || draft.isPending}>
          {editing ? (
            <MarkdownEditor
              value={text}
              onChange={(value) => {
                setText(value);
                query.setQueryData(draftKey, value);
                draft.reset();
              }}
            />
          ) : (
            <RenderedMarkdown
              assetBaseUrl={`/api/v2/runs/${issue.run_id}/artifacts`}
              markdown={issue.kind === "figure" ? figurePreview(text) : text}
            />
          )}
        </div>
      </article>
      <aside aria-label="原书对照">
        {issue.images.map((src, index) => (
          <figure key={src}>
            <a href={src} target="_blank" rel="noreferrer">
              打开原图 · 第 {issue.pages[index] ?? issue.page} 页
            </a>
            <img
              src={src}
              alt={`原书第 ${issue.pages[index] ?? issue.page} 页`}
              loading="lazy"
            />
          </figure>
        ))}
      </aside>
    </div>
  );
}

function reviewReason(reason: string) {
  return reason
    .replaceAll("deepseek-review-incomplete", "本地视觉核验未完成")
    .replaceAll(
      "independent-source-reading-failed",
      "原图独立初读未获得有效结果",
    )
    .replaceAll("review-failed", "核验未获得有效结果");
}

function figurePreview(text: string) {
  return text
    .replace(/^\s*Image\s*$/gm, "")
    .replace(/!\[[^\]]*\]\([^\n]*\)/g, "\n> 插图见右侧原书对照。\n");
}
