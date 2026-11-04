import { useEffect, useRef, useState, useMemo } from "react";
import { readLocations, resolveLocation } from "@/editor/review-highlights.ts";
import ReviewFocus, { type Resolution } from "./ReviewFocus.tsx";
import SourceFocus from "./SourceFocus.tsx";
import { useOutletContext } from "react-router-dom";
import type { WorkspaceContext } from "@/app/AppShell.tsx";
import {
  useMutation,
  useIsMutating,
  useQuery,
  useQueryClient,
  queryOptions,
} from "@tanstack/react-query";
import { Button } from "@/components/ui/button.tsx";
import { RenderedMarkdown } from "@/editor/RenderedMarkdown.tsx";
import { MarkdownEditor } from "@/editor/LazyMarkdownEditor.tsx";
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
}: {
  runId: string;
  stage?: string;
  state?: string;
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
              key={current.data.id}
              issue={current.data}
              workflowTest={workflowTest}
              onSaved={saved}
              onNext={() => {
                const items = issues.data ?? [];
                const i = items.findIndex((e) => e.id === currentId);
                setSelected(items[(i + 1) % items.length]?.id ?? null);
                setMessage(
                  items.length > 1
                    ? "本页保留待核对，已打开下一页。"
                    : "本页已保留待核对，当前没有其他待办。",
                );
              }}
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
  workflowTest,
  onNext,
}: {
  issue: components["schemas"]["ReviewIssue"];
  onSaved: (receipt: components["schemas"]["ReviewReceipt"]) => void;
  workflowTest: boolean;
  onNext: () => void;
}) {
  const query = useQueryClient(),
    draftKey = ["platform", "review-edit", issue.id];
  const [editing, setEditing] = useState(Boolean(issue.draft_text)),
    [sourceMode, setSourceMode] = useState(false),
    [text, setText] = useState(
      query.getQueryData<string>(draftKey) ?? issue.draft_text ?? issue.text,
    );
  const revisionRef = useRef(issue.revision),
    savedText = useRef(issue.draft_text ?? issue.text),
    latestText = useRef(text),
    queue = useRef<Promise<unknown>>(Promise.resolve());
  const baseLocations = useMemo(
    () => readLocations(issue.locations ?? []),
    [issue.locations],
  );
  const [active, setActive] = useState(baseLocations[0]?.id ?? ""),
    [boxes, setBoxes] = useState<Record<string, number[]>>({});
  const locations = useMemo(
    () =>
      baseLocations.map((l) =>
        boxes[l.id]
          ? { ...l, boxes: [boxes[l.id]], image_precision: "region" }
          : l,
      ),
    [baseLocations, boxes],
  );
  const current = locations.find((l) => l.id === active) ?? locations[0];
  const [resolutions, setResolutions] = useState<Resolution[]>(
    issue.resolutions ?? [],
  );
  const latestResolutions = useRef(resolutions),
    savedResolutions = useRef(JSON.stringify(issue.resolutions ?? []));
  latestResolutions.current = resolutions;
  const sourceArea = useRef<HTMLTextAreaElement>(null);
  function changeText(value: string) {
    setText(value);
    const retained = resolutions.filter((r) => {
      const location = locations.find((l) => l.id === r.id);
      if (!location) return false;
      const before = resolveLocation(text, location),
        after = resolveLocation(value, location);
      return (
        before &&
        after &&
        text.slice(before.start, before.end) ===
          value.slice(after.start, after.end)
      );
    });
    setResolutions(retained);
    query.setQueryData(draftKey, value);
    draft.reset();
    return retained;
  }
  function selectIssue(id: string) {
    setActive(id);
    if (editing && sourceMode) {
      const selected = locations.find((l) => l.id === id),
        range = selected && resolveLocation(text, selected);
      if (range)
        setTimeout(() => {
          sourceArea.current?.focus();
          sourceArea.current?.setSelectionRange(range.start, range.end);
        }, 0);
    }
  }
  function editIssue() {
    setEditing(true);
    setSourceMode(true);
    setTimeout(() => {
      const range = current && resolveLocation(latestText.current, current);
      if (range) {
        sourceArea.current?.focus();
        sourceArea.current?.setSelectionRange(range.start, range.end);
      }
    }, 0);
  }
  function resolve(action: Resolution["action"], replacement?: string) {
    if (!current) return;
    let next = resolutions.filter((r) => r.id !== current.id);
    if (action === "adopt" && replacement !== undefined) {
      const range = resolveLocation(text, current);
      if (!range) return;
      const value =
        text.slice(0, range.start) + replacement + text.slice(range.end);
      next = changeText(value).filter((r) => r.id !== current.id);
      setEditing(true);
      setSourceMode(true);
    }
    setResolutions([...next, { id: current.id, action }]);
  }
  latestText.current = text;
  const draft = useMutation({
    mutationKey: ["platform", "review-write", issue.run_id],
    mutationFn: async ({
      snapshot,
      decisions,
    }: {
      snapshot: string;
      decisions: Resolution[];
    }) => {
      if (
        snapshot === savedText.current &&
        JSON.stringify(decisions) === savedResolutions.current
      )
        return {
          revision: revisionRef.current,
          draft_text: snapshot,
          resolutions: decisions,
        };
      const { data, response } = await client.PUT(
        "/api/v2/reviews/{issue_id}/draft",
        {
          params: { path: { issue_id: issue.id } },
          body: {
            text: snapshot,
            expected_revision: revisionRef.current,
            resolutions: decisions,
          },
        },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "草稿版本已变化，请重新读取。"
            : "草稿暂未保存，请重试。",
        );
      revisionRef.current = data.revision;
      savedText.current = data.draft_text;
      savedResolutions.current = JSON.stringify(decisions);
      return data;
    },
    onSuccess: (data) => {
      query.setQueryData(issueOptions(issue.id).queryKey, {
        ...issue,
        revision: data.revision,
        draft_text: data.draft_text,
        resolutions: data.resolutions,
      });
    },
  });
  function saveDraft(snapshot: string) {
    const decisions = latestResolutions.current;
    const next = queue.current
      .catch(() => undefined)
      .then(() => draft.mutateAsync({ snapshot, decisions }));
    queue.current = next;
    return next;
  }
  const saveRef = useRef(saveDraft);
  saveRef.current = saveDraft;
  useEffect(() => {
    if (
      (text === savedText.current &&
        JSON.stringify(resolutions) === savedResolutions.current) ||
      draft.isError
    )
      return;
    const timer = setTimeout(() => {
      void saveRef.current(text).catch(() => undefined);
    }, 800);
    return () => clearTimeout(timer);
  }, [text, resolutions, draft.isError]);
  useEffect(
    () => () => {
      if (
        latestText.current !== savedText.current ||
        JSON.stringify(latestResolutions.current) !== savedResolutions.current
      )
        void saveRef.current(latestText.current).catch(() => undefined);
    },
    [],
  );
  const request = useRef<components["schemas"]["ReviewDecision"] | null>(null);
  const changed = text !== issue.text;
  const mutation = useMutation({
    mutationKey: ["platform", "review-write", issue.run_id],
    mutationFn: async () => {
      await saveDraft(text);
      const action = changed ? "correct" : "confirm";
      if (
        !request.current ||
        request.current.expected_revision !== revisionRef.current ||
        request.current.action !== action ||
        request.current.text !== (changed ? text : undefined)
      )
        request.current = {
          decision_id: crypto.randomUUID(),
          expected_revision: revisionRef.current,
          expected_text_sha256: issue.text_sha256,
          action,
          ...(changed ? { text } : {}),
          ...(locations.length ? { resolutions } : {}),
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
        <div className="review-sticky-controls">
          <div className="platform-review-actions">
            <strong>原件第 {issue.pages.join("、")} 页</strong>
            <Button
              disabled={
                mutation.isPending ||
                draft.isPending ||
                issue.state !== "pending" ||
                locations.some(
                  (l) =>
                    !resolutions.some(
                      (r) => r.id === l.id && r.action !== "defer",
                    ),
                )
              }
              onClick={() => mutation.mutate()}
            >
              {mutation.isPending
                ? "正在保存…"
                : workflowTest
                  ? changed
                    ? "保存修改并放行流程"
                    : "按授权放行流程"
                  : changed
                    ? "修改并确认"
                    : "确认本页"}
            </Button>
            <Button
              variant="outline"
              disabled={mutation.isPending || draft.isPending}
              onClick={() => setEditing(!editing)}
            >
              {editing ? "返回阅读" : "编辑正文"}
            </Button>
            {issue.proposed_text && !changed && (
              <Button
                variant="outline"
                disabled={mutation.isPending || draft.isPending}
                onClick={() => {
                  changeText(issue.proposed_text!);
                  setEditing(true);
                  query.setQueryData(draftKey, issue.proposed_text);
                }}
              >
                采用模型建议并编辑
              </Button>
            )}
            {editing && (
              <Button
                variant="outline"
                disabled={mutation.isPending}
                onClick={() => setSourceMode(!sourceMode)}
              >
                {sourceMode ? "可视化编辑" : "Markdown 源码"}
              </Button>
            )}
            {editing && (
              <Button
                variant="outline"
                disabled={mutation.isPending || draft.isPending}
                onClick={() => {
                  void saveDraft(text).catch(() => undefined);
                }}
              >
                {draft.isPending ? "正在存草稿…" : "保存草稿"}
              </Button>
            )}
            <Button
              variant="outline"
              disabled={mutation.isPending || draft.isPending}
              onClick={() => {
                void saveDraft(text)
                  .then(onNext)
                  .catch(() => undefined);
              }}
            >
              暂不确定，下一页
            </Button>
          </div>
          {mutation.isError && <p role="alert">{mutation.error.message}</p>}
          {draft.isError && <p role="alert">{draft.error.message}</p>}
          {draft.isSuccess && <p role="status">草稿已保存，尚未确认放行。</p>}
          {current && (
            <ReviewFocus
              locations={locations}
              active={current.id}
              onSelect={selectIssue}
              text={text}
              resolutions={resolutions}
              onResolve={resolve}
              onEdit={editIssue}
              disabled={mutation.isPending}
            />
          )}
        </div>
        <p className="platform-inline-status">
          需要核对：
          {issue.reasons[0]
            ? reviewReason(issue.reasons[0]).slice(0, 160)
            : "请对照原件确认本页。"}
        </p>
        <details>
          <summary>完整机器报告 · {issue.reasons.length} 项</summary>
          <ul className="platform-review-reasons">
            {issue.reasons.map((reason, index) => (
              <li key={index}>
                {reviewReason(reason)}
                {typeof reason !== "string" && (
                  <pre>{JSON.stringify(reason, null, 2)}</pre>
                )}
              </li>
            ))}
          </ul>
        </details>
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
        <div inert={mutation.isPending}>
          {editing && sourceMode ? (
            <textarea
              ref={sourceArea}
              className="platform-markdown-source"
              aria-label="Markdown 源码"
              spellCheck={false}
              rows={24}
              value={text}
              onChange={(event) => {
                changeText(event.target.value);
              }}
            />
          ) : editing ? (
            <MarkdownEditor
              value={text}
              highlights={locations}
              activeHighlight={current?.id}
              onHighlight={selectIssue}
              onChange={(value) => {
                changeText(value);
              }}
            />
          ) : (
            <RenderedMarkdown
              footnotes={text === issue.text ? issue.footnotes : []}
              assetBaseUrl={`/api/v2/runs/${issue.run_id}/artifacts`}
              markdown={issue.kind === "figure" ? figurePreview(text) : text}
              highlights={locations}
              activeHighlight={current?.id}
              onHighlight={selectIssue}
            />
          )}
        </div>
      </article>
      <aside aria-label="原书对照">
        {current && issue.images[issue.pages.indexOf(current.page)] ? (
          <SourceFocus
            src={issue.images[issue.pages.indexOf(current.page)]}
            issue={current}
            locations={locations}
            onSelect={selectIssue}
            onBox={(b) => setBoxes((old) => ({ ...old, [current.id]: b }))}
          />
        ) : (
          issue.images.map((src, index) => (
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
          ))
        )}
      </aside>
    </div>
  );
}

function reviewReason(
  reason: components["schemas"]["ReviewIssue"]["reasons"][number],
): string {
  if (typeof reason !== "string") {
    const field = (...keys: string[]) =>
      keys.map((k) => reason[k]).find((v) => typeof v === "string") as
        string | undefined;
    return (
      [
        field("location"),
        field("issue", "description", "explanation", "reason", "message"),
        field(
          "source_quote",
          "source_text",
          "source_reading",
          "original",
          "source",
        )
          ? `审核读法：${field("source_quote", "source_text", "source_reading", "original", "source")}`
          : "",
        field("candidate_quote", "candidate_text", "candidate")
          ? `候选文字：${field("candidate_quote", "candidate_text", "candidate")}`
          : "",
      ]
        .filter(Boolean)
        .join("；") || "请对照原件核对，详情见机器报告。"
    );
  }
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
