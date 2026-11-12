import { Button } from "@/components/ui/button.tsx";
import type { components } from "@/client/schema.d.ts";
import { RenderedMarkdown } from "@/editor/RenderedMarkdown.tsx";
import { MarkdownEditor } from "@/editor/LazyMarkdownEditor.tsx";
import { useReviewSession } from "./useReviewSession.ts";
import ReviewFocus from "./ReviewFocus.tsx";
import SourceFocus from "./SourceFocus.tsx";
import { useEffect, useRef, useState } from "react";
import type { RuntimeScope } from "@/app/runtime-scope.ts";

type Issue = components["schemas"]["ReviewIssue"];
export default function IssueEditor({
  issue,
  onSaved,
  workflowTest,
  runtimeScope = workflowTest ? "workflow_only" : "content",
}: {
  issue: Issue;
  workflowTest: boolean;
  runtimeScope?: RuntimeScope;
  onSaved: (
    receipt: components["schemas"]["ReviewReceipt"],
  ) => void | Promise<void>;
}) {
  const [unmapped, setUnmapped] = useState<string[]>([]);
  const [focusRequest, setFocusRequest] = useState(0);
  const workspace = useRef<HTMLDivElement>(null);
  const preview = useRef<HTMLElement>(null);
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
    draftConflict,
    remoteDraft,
    resolveRemote,
    lastDoubt,
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
  } = useReviewSession(issue, onSaved, runtimeScope);
  const previousLocation = useRef<{ id?: string; request: number }>({
    request: -1,
  });
  const submitButton = useRef<HTMLButtonElement>(null);
  const previouslyReady = useRef(ready);
  useEffect(() => {
    if (ready && !previouslyReady.current) submitButton.current?.focus();
    previouslyReady.current = ready;
  }, [ready]);
  function selectLocation(id: string) {
    setActive(id);
    setFocusRequest((value) => value + 1);
  }
  useEffect(() => {
    const element = workspace.current;
    if (!element) return;
    const fit = () => {
      const available =
        window.innerHeight - element.getBoundingClientRect().top - 24;
      (element.parentElement ?? element).style.setProperty(
        "--review-available-height",
        `${Math.max(360, available)}px`,
      );
    };
    fit();
    window.addEventListener("resize", fit);
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(fit);
    if (element.parentElement) observer?.observe(element.parentElement);
    return () => {
      window.removeEventListener("resize", fit);
      observer?.disconnect();
    };
  }, []);
  useEffect(() => {
    if (
      previousLocation.current.id === current?.id &&
      previousLocation.current.request === focusRequest
    )
      return;
    previousLocation.current = { id: current?.id, request: focusRequest };
    const frame = requestAnimationFrame(() => {
      const pane = preview.current;
      const mark = Array.from(
        pane?.querySelectorAll<HTMLElement>("mark[data-review-id]") ?? [],
      ).find((node) => node.dataset.reviewId === current?.id);
      if (pane && mark) {
        pane.scrollTo?.({
          top: Math.max(
            0,
            pane.scrollTop +
              mark.getBoundingClientRect().top -
              pane.getBoundingClientRect().top -
              pane.clientHeight / 2,
          ),
          behavior: "auto",
        });
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [current?.id, focusRequest]);
  const pageEditor = (
    <div className="review-whole-page-editor">
      <Button variant="outline" onClick={toggleSourceMode} disabled={busy}>
        {sourceMode ? "切换可视化编辑" : "切换 Markdown 源码"}
      </Button>
      {sourceMode ? (
        <label>
          整页 Markdown
          <textarea
            className="platform-markdown-source"
            aria-label="Markdown 源码"
            value={text}
            onChange={(event) => changeText(event.target.value)}
            rows={18}
            spellCheck={false}
            disabled={busy}
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
  );
  return (
    <div
      className="evidence-review-session"
      ref={workspace}
      onKeyDown={(event) => {
        if (
          !event.altKey ||
          event.ctrlKey ||
          event.metaKey ||
          event.shiftKey ||
          editing ||
          busy
        )
          return;
        if (
          event.target instanceof Element &&
          event.target.closest(
            'input, textarea, select, [contenteditable="true"], [role="textbox"]',
          )
        )
          return;
        if (event.key === "Enter" && ready) {
          event.preventDefault();
          submitButton.current?.click();
        } else if (["ArrowLeft", "ArrowRight"].includes(event.key) && current) {
          const index = locations.findIndex((item) => item.id === current.id);
          const next = locations[index + (event.key === "ArrowRight" ? 1 : -1)];
          event.preventDefault();
          if (next) selectLocation(next.id);
        }
      }}
    >
      <article>
        <header className="review-page-heading">
          <div>
            <h2>当前文字</h2>
            <small>原件第 {issue.pages.join("、")} 页</small>
          </div>
          <span>
            {locations.length
              ? `已处理 ${completed} / ${locations.length} 处`
              : "整页核对"}
          </span>
        </header>
        {draftConflict && (
          <section className="review-draft-conflict" role="note">
            <p>
              机器重新核对后，底稿或疑点已更新，原草稿已保留。请结合当前原件和新底稿重新核对，再提交本页。
            </p>
            <details>
              <summary>查看机器重试后的新底稿</summary>
              <pre>{issue.text}</pre>
            </details>
          </section>
        )}
        {remoteDraft && (
          <section className="review-draft-conflict" role="alert">
            <p>服务器草稿已有更新，本页输入已保留。请选择要继续核对的版本。</p>
            <details>
              <summary>查看服务器草稿</summary>
              <pre>{remoteDraft.draft_text ?? remoteDraft.text}</pre>
            </details>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => resolveRemote(true)}
            >
              保留本页输入继续核对
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => resolveRemote(false)}
            >
              读取服务器草稿
            </Button>
          </section>
        )}
        <section
          ref={preview}
          className="review-page-preview"
          aria-label="整页文字与上下文"
        >
          {issue.kind === "figure" && (
            <p>本项为插图或地图，请核对原图及图说；图内标签保留在原件中。</p>
          )}
          <div inert={submission.isPending}>
            {editing && wholePage ? (
              pageEditor
            ) : (
              <>
                <RenderedMarkdown
                  markdown={
                    issue.kind === "figure" ? figurePreview(text) : text
                  }
                  highlights={locations}
                  activeHighlight={current?.id}
                  onHighlight={editing || busy ? undefined : selectLocation}
                  footnotes={text === issue.text ? issue.footnotes : []}
                  onLocationFailure={setUnmapped}
                  assetBaseUrl={`/api/v2/runs/${issue.run_id}/artifacts`}
                />
                {current && unmapped.includes(current.id) && (
                  <section
                    className="review-source-text"
                    aria-label="当前疑点的原文定位"
                  >
                    <p>
                      阅读视图暂不能标记本处，请对照以下 Markdown 原文片段。
                    </p>
                    <pre>
                      <mark>
                        {current.anchor.exact ||
                          current.candidate ||
                          "请查看整页 Markdown"}
                      </mark>
                    </pre>
                  </section>
                )}
              </>
            )}
          </div>
        </section>
      </article>
      <aside aria-label="原书对照">
        {current && issue.images[issue.pages.indexOf(current.page)] ? (
          <SourceFocus
            src={issue.images[issue.pages.indexOf(current.page)]}
            issue={current}
            locations={locations}
            onSelect={editing || busy ? undefined : selectLocation}
            onBox={(box) => setBoxes((old) => ({ ...old, [current.id]: box }))}
          />
        ) : issue.images.length ? (
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
        ) : (
          <div className="evidence-review-no-source" role="status">
            <h3>原图暂不可用</h3>
            <p>本页文字和草稿已保留。重新打开本页取得原图后再核对。</p>
          </div>
        )}
      </aside>
      <section className="evidence-review-actions" aria-label="当前疑点与修改">
        {ready && !editing ? (
          <section className="review-page-complete" aria-label="本页提交">
            <h3>本页疑点已处理，等待提交</h3>
            <p>
              {changed
                ? "文字已修改。请检查最终文字，提交后本页才计入已通过。"
                : "已记录保留当前文字的判断，提交后本页才计入已通过。"}
            </p>
            <Button variant="outline" disabled={busy} onClick={recheckPage}>
              重新检查当前页
            </Button>
          </section>
        ) : current ? (
          <ReviewFocus
            locations={locations}
            active={current.id}
            onSelect={selectLocation}
            text={text}
            resolutions={resolutions}
            disabled={busy}
            editing={editing}
            machineReasons={issue.reasons.map(reasonText)}
          />
        ) : (
          <section className="review-focus">
            <h3>对照原图核对整页</h3>
            <p>本页没有可靠的局部定位，请检查正文与原图。</p>
          </section>
        )}
        {editing && (
          <section
            className="review-edit-step"
            aria-label="修改当前内容"
            inert={busy}
          >
            {!wholePage && editRange ? (
              <>
                <label>
                  本处最终文字
                  <textarea
                    aria-label="本处最终文字"
                    autoFocus
                    disabled={busy}
                    value={text.slice(editRange.start, editRange.end)}
                    onChange={(e) => changeFocus(e.target.value)}
                    rows={2}
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
              <p>
                在上方正文区编辑整页，原件仍在右侧。修改完成后保存，再提交本页。
              </p>
            )}
          </section>
        )}
        {issue.reasons.length > 0 && (!current || ready || editing) && (
          <details className="review-machine-report">
            <summary>查看机器核对依据 · {issue.reasons.length} 项</summary>
            <ul>
              {issue.reasons.map((r, i) => (
                <li key={i}>{reasonText(r)}</li>
              ))}
            </ul>
          </details>
        )}
      </section>

      <footer
        className="review-page-footer"
        role="group"
        aria-label="当前决定与提交"
      >
        <div className="review-footer-state">
          {draft.isError || submission.isError ? (
            <p role="alert">
              {draft.error?.message ?? submission.error?.message}
            </p>
          ) : (
            <p role="status">
              {draft.isPending || finishing
                ? "正在保存草稿，请等待保存完成。"
                : draft.isSuccess
                  ? "草稿已自动保存 · 尚未提交本页"
                  : issue.draft_text
                    ? "已恢复草稿，尚未提交本页审核结果。"
                    : "逐处核对后提交本页"}
            </p>
          )}
          <small>
            {ready
              ? "本页判断已完成，请检查最终文字。"
              : "保留或修改会保存草稿；提交后本页才计入已通过。"}
          </small>
          <small className="review-shortcuts">
            Alt+← / → 切换疑点 · 处理完成后 Alt+Enter 提交
          </small>
        </div>
        {draft.isError && (
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void saveDraft(text).catch(() => undefined)}
          >
            重试保存草稿
          </Button>
        )}
        {editing ? (
          <div className="review-footer-actions">
            <Button variant="outline" disabled={busy} onClick={cancelEdit}>
              取消修改
            </Button>
            <Button
              disabled={busy || !text.trim()}
              onClick={() => {
                void finish("edit").catch(() => undefined);
              }}
            >
              {finishing
                ? "正在保存…"
                : lastDoubt
                  ? "保存修改并检查本页"
                  : "保存修改并继续"}
            </Button>
          </div>
        ) : !ready ? (
          <div className="review-footer-actions">
            <Button variant="outline" disabled={busy} onClick={beginEdit}>
              需要修改
            </Button>
            <Button
              disabled={busy}
              onClick={() => {
                void finish("keep").catch(() => undefined);
              }}
            >
              识别正确，保留
            </Button>
          </div>
        ) : (
          <Button
            ref={submitButton}
            aria-keyshortcuts="Alt+Enter"
            disabled={
              !ready ||
              editing ||
              busy ||
              draft.isPending ||
              draft.isError ||
              runtimeScope === "unknown" ||
              issue.state !== "pending"
            }
            onClick={() => submission.mutate()}
          >
            {runtimeScope === "unknown"
              ? "确认工作区范围后再提交"
              : submission.isPending
                ? "正在提交…"
                : submission.isError
                  ? "重试提交本页"
                  : workflowTest
                    ? "按授权提交流程测试结果"
                    : "提交本页并继续"}
          </Button>
        )}
      </footer>
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
