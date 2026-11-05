import { Button } from "@/components/ui/button.tsx";
import type { components } from "@/client/schema.d.ts";
import { RenderedMarkdown } from "@/editor/RenderedMarkdown.tsx";
import { MarkdownEditor } from "@/editor/LazyMarkdownEditor.tsx";
import { decisionRange } from "@/editor/review-highlights.ts";
import { useReviewSession } from "./useReviewSession.ts";
import ReviewFocus from "./ReviewFocus.tsx";
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
  const {
    text,
    resolutions,
    locations,
    current,
    editing,
    wholePage,
    editRange,
    finishing,
    sourceMode,
    draft,
    ready,
    proposal,
    changed,
    submission,
    busy,
    completed,
    recheckPage,
    setActive,
    finish,
    beginEdit,
    changeFocus,
    editWholePage,
    toggleSourceMode,
    changeText,
    cancelEdit,
    saveDraft,
    setBoxes,
  } = useReviewSession(issue, onSaved);
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
            <h3>本页疑点已处理，等待提交</h3>
            <p>
              {changed
                ? "草稿已保存。请检查最终文字，提交后本页才计入已通过。"
                : "已记录保留当前文字的判断，提交后本页才计入已通过。"}
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
                  : "提交本页并继续"}
            </Button>
            <Button variant="outline" disabled={busy} onClick={recheckPage}>
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
            <h3>对照原图核对整页</h3>
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
                  onClick={editWholePage}
                >
                  编辑整页 Markdown
                </button>
              </>
            ) : (
              <div>
                <Button variant="outline" onClick={toggleSourceMode}>
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
                if (draft.isError) void saveDraft(text).catch(() => undefined);
                else submission.mutate();
              }}
            >
              {draft.isError ? "重试保存草稿" : "重试提交本页"}
            </Button>
          </p>
        )}
        {(draft.isPending || finishing) && (
          <p role="status" className="review-save-status">
            正在保存草稿，请等待保存完成。
          </p>
        )}
        {draft.isSuccess && !draft.isPending && !finishing && (
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
    .replaceAll("deepseek-review-incomplete", "原图核对未完成")
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
