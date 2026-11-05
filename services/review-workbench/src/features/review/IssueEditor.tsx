import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button.tsx";
import { client } from "@/client/api.ts";
import type { components } from "@/client/schema.d.ts";
import { RenderedMarkdown } from "@/editor/RenderedMarkdown.tsx";
import { MarkdownEditor } from "@/editor/LazyMarkdownEditor.tsx";
import {
  decisionRange,
  readLocations,
  suggestionEdit,
  moveReviewLocations,
} from "@/editor/review-highlights.ts";
import ReviewFocus, { type Resolution } from "./ReviewFocus.tsx";
import SourceFocus from "./SourceFocus.tsx";

type Issue = components["schemas"]["ReviewIssue"];
export default function IssueEditor({
  issue,
  onSaved,
  workflowTest,
}: {
  issue: Issue;
  workflowTest: boolean;
  onSaved: (receipt: components["schemas"]["ReviewReceipt"]) => void;
}) {
  const query = useQueryClient(),
    draftKey = ["platform", "review-edit", issue.id];
  const [text, setText] = useState(
    query.getQueryData<string>(draftKey) ?? issue.draft_text ?? issue.text,
  );
  const [resolutions, setResolutions] = useState<Resolution[]>(
    issue.resolutions ?? [],
  );
  const baseLocations = useMemo(
    () => readLocations(issue.locations ?? []),
    [issue.locations],
  );
  const [textLocations, setTextLocations] = useState(() =>
    moveReviewLocations(issue.text, text, baseLocations),
  );
  const [active, setActive] = useState(
    () =>
      baseLocations.find(
        (l) =>
          !issue.resolutions?.some(
            (r) => r.id === l.id && r.action !== "defer",
          ),
      )?.id ??
      baseLocations[0]?.id ??
      "",
  );
  const [boxes, setBoxes] = useState<Record<string, number[]>>({});
  const locations = useMemo(
    () =>
      textLocations.map((l) =>
        boxes[l.id]
          ? { ...l, boxes: [boxes[l.id]], image_precision: "region" }
          : l,
      ),
    [textLocations, boxes],
  );
  const current = locations.find((l) => l.id === active) ?? locations[0];
  const [editing, setEditing] = useState(false),
    [wholePage, setWholePage] = useState(false),
    [pageReviewed, setPageReviewed] = useState(false);
  const [editRange, setEditRange] = useState<{
    start: number;
    end: number;
  } | null>(null);
  const [finishing, setFinishing] = useState(false);
  const [sourceMode, setSourceMode] = useState(false);
  const snapshot = useRef({ text, resolutions, locations: textLocations });
  const latestText = useRef(text),
    latestResolutions = useRef(resolutions);
  const savedText = useRef(issue.draft_text ?? issue.text),
    savedResolutions = useRef(JSON.stringify(issue.resolutions ?? []));
  const revision = useRef(issue.revision),
    queue = useRef<Promise<unknown>>(Promise.resolve());
  latestText.current = text;
  latestResolutions.current = resolutions;
  const draft = useMutation({
    mutationKey: ["platform", "review-write", issue.run_id],
    mutationFn: async ({
      value,
      decisions,
    }: {
      value: string;
      decisions: Resolution[];
    }) => {
      if (
        value === savedText.current &&
        JSON.stringify(decisions) === savedResolutions.current
      )
        return {
          revision: revision.current,
          draft_text: value,
          resolutions: decisions,
        };
      const { data, response } = await client.PUT(
        "/api/v2/reviews/{issue_id}/draft",
        {
          params: { path: { issue_id: issue.id } },
          body: {
            text: value,
            expected_revision: revision.current,
            resolutions: decisions,
          },
        },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "草稿版本已变化，请重新读取；当前文字已保留。"
            : "草稿未保存，请重试。",
        );
      revision.current = data.revision;
      savedText.current = data.draft_text;
      savedResolutions.current = JSON.stringify(decisions);
      return data;
    },
    onSuccess: (data) =>
      query.setQueryData(["platform", "issue", issue.id], {
        ...issue,
        revision: data.revision,
        draft_text: data.draft_text,
        resolutions: data.resolutions,
      }),
  });
  function saveDraft(value: string, decisions = latestResolutions.current) {
    const next = queue.current
      .catch(() => undefined)
      .then(() => draft.mutateAsync({ value, decisions }));
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

  function changeText(value: string) {
    const nextLocations = moveReviewLocations(text, value, textLocations);
    const retained = resolutions.filter((r) => {
      const location = locations.find((l) => l.id === r.id);
      if (!location) return false;
      const before = decisionRange(text, location),
        after = decisionRange(
          value,
          nextLocations.find((l) => l.id === r.id) ?? location,
        );
      return (
        before &&
        after &&
        text.slice(before.start, before.end) ===
          value.slice(after.start, after.end)
      );
    });
    setText(value);
    latestText.current = value;
    setResolutions(retained);
    latestResolutions.current = retained;
    setTextLocations(nextLocations);
    setPageReviewed(false);
    query.setQueryData(draftKey, value);
    draft.reset();
  }
  function beginEdit() {
    snapshot.current = { text, resolutions, locations: textLocations };
    const range = current ? decisionRange(text, current) : null;
    setEditRange(range);
    setWholePage(!range);
    setSourceMode(false);
    setEditing(true);
  }
  function changeFocus(value: string) {
    if (!editRange) return;
    changeText(
      text.slice(0, editRange.start) + value + text.slice(editRange.end),
    );
    setEditRange({ ...editRange, end: editRange.start + value.length });
  }
  function cancelEdit() {
    const previous = snapshot.current;
    setText(previous.text);
    latestText.current = previous.text;
    query.setQueryData(draftKey, previous.text);
    setResolutions(previous.resolutions);
    latestResolutions.current = previous.resolutions;
    setTextLocations(previous.locations);
    setEditing(false);
    setEditRange(null);
    void saveDraft(previous.text, previous.resolutions).catch(() => undefined);
  }
  async function finish(action: Resolution["action"]) {
    if (finishing) return;
    setFinishing(true);
    const decisions = current
      ? [
          ...resolutions.filter((r) => r.id !== current.id),
          { id: current.id, action },
        ]
      : resolutions;
    setResolutions(decisions);
    latestResolutions.current = decisions;
    try {
      await saveDraft(text, decisions);
      setEditing(false);
      setEditRange(null);
      setPageReviewed(true);
      const next = locations.find(
        (l) => !decisions.some((r) => r.id === l.id && r.action !== "defer"),
      );
      if (next) setActive(next.id);
    } finally {
      setFinishing(false);
    }
  }
  const ready = locations.length
    ? locations.every((l) =>
        resolutions.some((r) => r.id === l.id && r.action !== "defer"),
      )
    : pageReviewed;
  const originalLocation = baseLocations.find((l) => l.id === current?.id);
  const proposal = originalLocation
    ? suggestionEdit(issue.text, originalLocation)
    : null;
  const request = useRef<components["schemas"]["ReviewDecision"] | null>(null);
  const changed = text !== issue.text;
  const submission = useMutation({
    mutationKey: ["platform", "review-write", issue.run_id],
    mutationFn: async () => {
      await saveDraft(text);
      const action = changed ? "correct" : "confirm";
      if (
        !request.current ||
        request.current.expected_revision !== revision.current ||
        request.current.text !== (changed ? text : undefined) ||
        JSON.stringify(request.current.resolutions ?? []) !==
          JSON.stringify(resolutions)
      )
        request.current = {
          decision_id: crypto.randomUUID(),
          expected_revision: revision.current,
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
            ? "内容或版本已变化，请重新读取；当前草稿已保留。"
            : "提交未确认，请重试；草稿已保留。",
        );
      return data;
    },
    onSuccess: onSaved,
  });
  const busy = finishing || submission.isPending;
  const completed = resolutions.filter(
    (r) => r.action !== "defer" && locations.some((l) => l.id === r.id),
  ).length;
  return (
    <div className="platform-review-content guided-review">
      <article>
        <header className="review-page-heading">
          <div>
            <small>人工终审</small>
            <h2>原件第 {issue.pages.join("、")} 页</h2>
          </div>
          <span>
            {locations.length
              ? `已处理 ${completed} / ${locations.length} 处`
              : "整页核对"}
          </span>
        </header>
        {issue.draft_text && !draft.isSuccess && (
          <p className="review-save-status">
            已恢复草稿，尚未提交本页审核结果。
          </p>
        )}
        {ready && !editing ? (
          <section className="review-page-complete" aria-label="本页提交">
            <h3>本页核对已完成</h3>
            <p>
              {changed
                ? "修改已保存，请检查最终文字后提交本页。"
                : "你已确认保留当前识别文字。"}
            </p>
            <Button
              disabled={
                busy ||
                draft.isPending ||
                draft.isError ||
                issue.state !== "pending"
              }
              onClick={() => submission.mutate()}
            >
              {submission.isPending
                ? "正在提交…"
                : workflowTest
                  ? "按授权提交流程测试结果"
                  : "提交本页审核结果"}
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => {
                setPageReviewed(false);
                if (current) {
                  setResolutions(
                    resolutions.filter((r) => r.id !== current.id),
                  );
                  setActive(current.id);
                }
              }}
            >
              重新检查当前页
            </Button>
          </section>
        ) : current ? (
          <ReviewFocus
            locations={locations}
            active={current.id}
            onSelect={setActive}
            text={text}
            resolutions={resolutions}
            onResolve={(action) => {
              void finish(action).catch(() => undefined);
            }}
            onEdit={beginEdit}
            disabled={busy}
            editing={editing}
          />
        ) : (
          <section className="review-focus">
            <h3>对照右侧原图核对整页</h3>
            <p>本页没有可靠的局部定位，请检查正文与原图。</p>
            {!editing && (
              <div className="review-judgment-actions">
                <Button
                  disabled={busy}
                  onClick={() => {
                    void finish("keep").catch(() => undefined);
                  }}
                >
                  识别正确，保留
                </Button>
                <Button variant="outline" disabled={busy} onClick={beginEdit}>
                  需要修改
                </Button>
              </div>
            )}
          </section>
        )}
        {editing && (
          <section className="review-edit-step" aria-label="修改当前内容">
            {!wholePage && editRange ? (
              <>
                <label>
                  本处最终文字
                  <textarea
                    aria-label="本处最终文字"
                    autoFocus
                    value={text.slice(editRange.start, editRange.end)}
                    onChange={(e) => changeFocus(e.target.value)}
                    rows={3}
                  />
                </label>
                <p>只修改当前选中的文字。原件缺损时，可保留明确的缺字标记。</p>
                {proposal && (
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => changeFocus(proposal.replacement)}
                  >
                    填入机器建议
                  </Button>
                )}
                <button
                  className="review-text-link"
                  disabled={busy}
                  onClick={() => {
                    setWholePage(true);
                    setEditRange(null);
                    setSourceMode(true);
                  }}
                >
                  编辑整页 Markdown
                </button>
              </>
            ) : (
              <div>
                <Button
                  variant="outline"
                  onClick={() => setSourceMode(!sourceMode)}
                >
                  {sourceMode ? "切换可视化编辑" : "切换 Markdown 源码"}
                </Button>
                {sourceMode ? (
                  <label>
                    整页 Markdown
                    <textarea
                      className="platform-markdown-source"
                      aria-label="Markdown 源码"
                      value={text}
                      onChange={(e) => changeText(e.target.value)}
                      rows={18}
                      spellCheck={false}
                    />
                  </label>
                ) : (
                  <MarkdownEditor
                    value={text}
                    highlights={locations}
                    activeHighlight={current?.id}
                    onChange={changeText}
                  />
                )}
              </div>
            )}
            <div className="review-edit-actions">
              <Button
                disabled={busy || !text.trim()}
                onClick={() => {
                  void finish("edit").catch(() => undefined);
                }}
              >
                {finishing ? "正在保存…" : "保存修改并继续"}
              </Button>
              <Button variant="outline" disabled={busy} onClick={cancelEdit}>
                取消修改
              </Button>
            </div>
          </section>
        )}
        {(draft.isError || submission.isError) && (
          <p role="alert">
            {draft.error?.message ?? submission.error?.message}
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => {
                void saveDraft(text).catch(() => undefined);
              }}
            >
              重试保存草稿
            </Button>
          </p>
        )}
        {draft.isSuccess && (
          <p role="status" className="review-save-status">
            草稿已自动保存 · 尚未提交本页
          </p>
        )}
        <details
          className="review-page-preview"
          open={
            !locations.length ||
            ready ||
            Boolean(current && !decisionRange(text, current))
          }
        >
          <summary>
            {ready ? "检查本页最终文字" : "查看整页文字与上下文"}
          </summary>
          {issue.kind === "figure" && (
            <p>本项为插图或地图，请核对原图及图说；图内标签保留在原件中。</p>
          )}
          <div inert={submission.isPending}>
            <RenderedMarkdown
              markdown={issue.kind === "figure" ? figurePreview(text) : text}
              highlights={locations}
              activeHighlight={current?.id}
              onHighlight={editing ? undefined : setActive}
              footnotes={text === issue.text ? issue.footnotes : []}
              assetBaseUrl={`/api/v2/runs/${issue.run_id}/artifacts`}
            />
          </div>
        </details>
        <details className="review-machine-report">
          <summary>查看机器核对依据 · {issue.reasons.length} 项</summary>
          <ul>
            {issue.reasons.map((r, i) => (
              <li key={i}>{reasonText(r)}</li>
            ))}
          </ul>
        </details>
      </article>
      <aside aria-label="原书对照">
        {current && issue.images[issue.pages.indexOf(current.page)] ? (
          <SourceFocus
            src={issue.images[issue.pages.indexOf(current.page)]}
            issue={current}
            locations={locations}
            onSelect={editing ? undefined : setActive}
            onBox={(box) => setBoxes((old) => ({ ...old, [current.id]: box }))}
          />
        ) : (
          issue.images.map((src, i) => (
            <figure key={src}>
              <a href={src} target="_blank" rel="noreferrer">
                打开原图 · 第 {issue.pages[i] ?? issue.page} 页
              </a>
              <img
                src={src}
                alt={`原书第 ${issue.pages[i] ?? issue.page} 页`}
              />
            </figure>
          ))
        )}
      </aside>
    </div>
  );
}

function reasonText(reason: Issue["reasons"][number]) {
  if (typeof reason !== "string")
    return (
      [
        reason.location,
        reason.explanation ??
          reason.issue ??
          reason.description ??
          reason.reason ??
          reason.message,
      ]
        .filter((v) => typeof v === "string")
        .join("；") || "请对照原件核对。"
    );
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
    .replace(/!\[[^\]]*\]\([^\n]*\)/g, "\n> 插图见原书对照。\n");
}
