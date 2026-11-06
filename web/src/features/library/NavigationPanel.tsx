import { queryKeys } from "@/client/query-keys.ts";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/client/api.ts";
import { updateBooks } from "@/features/books/book-cache.ts";
import { Button } from "@/components/ui/button.tsx";
import { useRuntimeScope } from "@/app/runtime-scope.ts";
import NavigationReview, {
  type NavigationChoice,
} from "./NavigationReview.tsx";
import {
  navigationOptions,
  readingNavigationOptions,
} from "./navigation-query";
import { readingHref } from "./reader-location";
import {
  navigationCompleted as linked,
  navigationStatusLabel,
} from "./navigation-status";
export default function NavigationPanel({
  runId,
  bookId,
  bookState,
  reviewOnly = false,
  entryId,
  published = false,
}: {
  runId: string;
  bookId: string;
  bookState?: string;
  reviewOnly?: boolean;
  entryId?: string;
  published?: boolean;
}) {
  const query = useQueryClient();
  const runtimeScope = useRuntimeScope();
  const readingOnly = published && !reviewOnly;
  const report = useQuery(
    readingOnly ? readingNavigationOptions(bookId) : navigationOptions(runId),
  );
  const [selected, setSelected] = useState<string | null>(entryId ?? null),
    [filter, setFilter] = useState(reviewOnly ? "attention" : "all"),
    [message, setMessage] = useState(""),
    [restart, setRestart] = useState(0);
  const entries = report.data?.entries ?? [],
    attention = entries.filter((e) => e.needs_confirmation);
  const incompleteMessage =
    bookState === "paused"
      ? "自动处理已暂停。请在处理进展中继续处理，新的目录项目将在核对后列出。"
      : bookState === "failed"
        ? "自动处理已停止。请在处理进展中从断点恢复，新的目录项目将在核对后列出。"
        : ["running", "processing", "queued"].includes(bookState ?? "")
          ? "目录尚未全部核对，新发现的项目会自动列出。"
          : "目录尚未全部核对，请在处理进展中查看剩余页面的状态。";
  const selectedEntry = entries.find(
    (entry) =>
      entry.id === selected &&
      (!reviewOnly || entry.needs_confirmation || entry.id === entryId),
  );
  const activeId = readingOnly
    ? null
    : (selectedEntry?.id ?? (reviewOnly ? attention[0]?.id : null));
  const editor = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLOListElement>(null);
  useEffect(() => {
    setSelected(entryId ?? null);
  }, [entryId]);
  useEffect(() => {
    if (selected) editor.current?.scrollIntoView?.({ block: "start" });
  }, [selected]);
  useEffect(() => {
    const requested = new URLSearchParams(
      window.location.hash.split("?")[1] ?? "",
    ).get("tocEntry");
    if (requested && !reviewOnly)
      list.current
        ?.querySelector<HTMLElement>(
          `[data-toc-entry="${CSS.escape(requested)}"]`,
        )
        ?.scrollIntoView?.({ block: "center" });
  }, [report.data, reviewOnly]);
  const pendingDecision = useRef<{ signature: string; id: string } | null>(
    null,
  );
  const decision = useMutation({
    mutationFn: async ({
      choice,
      sha,
      id,
    }: {
      choice: NavigationChoice | null;
      sha: string;
      id: string;
    }) => {
      if (runtimeScope === "unknown")
        throw new Error("工作区范围未确认，请重新确认后提交目录决定。");
      const signature = JSON.stringify([id, sha, choice]);
      if (pendingDecision.current?.signature !== signature)
        pendingDecision.current = { signature, id: crypto.randomUUID() };
      const body = {
        decision_id: pendingDecision.current.id,
        expected_sha256: sha,
        target_page: choice?.page ?? null,
        target_anchor: choice?.anchor ?? "",
        decision_scope:
          runtimeScope === "workflow_only"
            ? ("workflow_test" as const)
            : ("content" as const),
      };
      const { data, response } = await client.POST(
        "/api/v2/runs/{run_id}/navigation/{entry_id}/decision",
        {
          params: { path: { run_id: runId, entry_id: id } },
          body,
        },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "目录或正文已更新。你的选择尚未提交，请重新载入证据后核对。"
            : "未能保存跳转决定，请重试；本次选择仍保留。",
        );
      return data;
    },
    onSuccess: (data, { choice, id }) => {
      pendingDecision.current = null;
      const title = entries.find((e) => e.id === id)?.title ?? "这条目录";
      setMessage(
        choice
          ? `已确认：《${title}》 → PDF 第 ${choice.page} 页。`
          : `已确认：《${title}》保留目录文字，不设置跳转。`,
      );
      query.setQueryData(queryKeys.navigation(runId), data);
      void query.invalidateQueries({
        queryKey: queryKeys.readingNavigation(bookId),
      });
      setSelected(
        reviewOnly
          ? (data.entries.find((e) => e.needs_confirmation)?.id ?? null)
          : null,
      );
      const navigation_pending_count = data.pending_count;
      updateBooks(
        query,
        (book) => book.id === bookId,
        (book) => ({ ...book, navigation_pending_count }),
      );
      // The next bridge event replaces these committed local counters after projection.
    },
  });
  const visible = reviewOnly
    ? entries.filter((e) => e.id === activeId)
    : entries.filter((e) =>
        filter === "attention"
          ? e.needs_confirmation
          : filter === "linked"
            ? linked(e)
            : filter === "waiting"
              ? !linked(e) && !e.needs_confirmation
              : true,
      );
  function choose(id: string) {
    setSelected(id);
    decision.reset();
    setRestart((v) => v + 1);
  }
  return (
    <section className="platform-navigation" data-review-only={reviewOnly}>
      {!reviewOnly && <h2>原书目录</h2>}
      {!reviewOnly && (
        <p>
          {readingOnly ? (
            <>
              目录与当前阅读版保持一致。
              <a href={`#/books/${bookId}?tab=review&task=navigation`}>
                核对目录对应位置
              </a>
            </>
          ) : (
            "点击目录可阅读正文；跳转位置不对时，可在当前条目重新核对。"
          )}
        </p>
      )}
      {message && (
        <p role="status" className="navigation-receipt">
          {message}
          {reviewOnly && attention.length > 0 ? " 已打开下一项目录。" : ""}
        </p>
      )}
      {report.isPending && <p role="status">正在读取目录…</p>}
      {report.isError && (
        <p role="alert">
          {report.error.message}
          <Button onClick={() => void report.refetch()}>重新读取目录</Button>
        </p>
      )}
      {report.data && (
        <>
          {reviewOnly ? (
            <p className="navigation-queue-status">
              {attention.length
                ? `还有 ${attention.length} 项目录需要核对。确认跳转只改变目录导航，正文与原件保留。`
                : "当前没有需要你确认的目录项目。"}
            </p>
          ) : (
            <div
              className="workspace-section-tabs"
              role="group"
              aria-label="目录状态"
            >
              {[
                ["attention", `需你核对 · ${attention.length}`],
                ["linked", `已设置或确认 · ${entries.filter(linked).length}`],
                [
                  "waiting",
                  `等待核验 · ${entries.filter((e) => !linked(e) && !e.needs_confirmation).length}`,
                ],
                ["all", `全部目录 · ${entries.length}`],
              ].map(([value, label]) => (
                <Button
                  key={value}
                  disabled={decision.isPending}
                  variant={filter === value ? "default" : "outline"}
                  onClick={() => {
                    setFilter(value);
                    setSelected(null);
                    decision.reset();
                  }}
                >
                  {label}
                </Button>
              ))}
            </div>
          )}
          {!visible.length && (
            <div className="workspace-empty">
              <h3>
                {reviewOnly && message
                  ? "当前目录核对完成"
                  : filter === "attention"
                    ? "当前没有需要核对的目录"
                    : "暂无目录条目"}
              </h3>
              <p>
                {report.data.coverage_complete
                  ? "已确认的跳转可以在原书目录中试用或修改。"
                  : incompleteMessage}
              </p>
              <a href={`#/books/${bookId}?tab=reader&section=directory`}>
                {message ? "试用目录跳转" : "浏览原书目录"}
              </a>
            </div>
          )}
          <div
            className={
              reviewOnly && activeId ? "navigation-review-layout" : undefined
            }
          >
            {reviewOnly && activeId && (
              <nav className="navigation-review-queue" aria-label="目录待办">
                <header>
                  <h2>目录待办</h2>
                  <p>{attention.length} 项需要确认</p>
                </header>
                <ol>
                  {entries
                    .filter(
                      (entry) =>
                        entry.needs_confirmation || entry.id === activeId,
                    )
                    .map((entry) => (
                      <li key={entry.id}>
                        <button
                          type="button"
                          aria-label={`选择目录：${entry.title}`}
                          aria-current={
                            entry.id === activeId ? "true" : undefined
                          }
                          disabled={decision.isPending}
                          onClick={() => choose(entry.id)}
                        >
                          <strong>{entry.title}</strong>
                          <small>书内页 {entry.target_label || "未标示"}</small>
                          <span>{navigationStatusLabel(entry)}</span>
                        </button>
                      </li>
                    ))}
                </ol>
                <a href={`#/books/${bookId}?tab=reader&section=directory`}>
                  浏览原书目录
                </a>
              </nav>
            )}
            <ol
              ref={list}
              aria-label="原书目录"
              className="navigation-entry-list"
            >
              {visible.map((e) => (
                <li
                  key={e.id}
                  data-toc-entry={e.id}
                  data-selected={activeId === e.id}
                >
                  {activeId !== e.id && (
                    <>
                      <header>
                        <div>
                          <strong>{e.title}</strong>
                          <p>
                            书内页码 {e.target_label}
                            {e.target_end_label ? `–${e.target_end_label}` : ""}
                          </p>
                        </div>
                        <span className="workspace-status-tag">
                          {navigationStatusLabel(e)}
                        </span>
                      </header>
                      <div className="navigation-entry-links">
                        {e.target && (
                          <a
                            href={
                              e.reading_target
                                ? `#${readingHref(bookId, e.reading_target, { returnTo: `/books/${bookId}?tab=reader&section=directory&tocEntry=${encodeURIComponent(e.id)}` })}`
                                : (e.target_image ?? e.target_url ?? undefined)
                            }
                            target={e.reading_target ? undefined : "_blank"}
                            rel="noreferrer"
                          >
                            {e.reading_target
                              ? "阅读正文 →"
                              : `查看正文原图 · PDF 第 ${e.target.physical_page} 页`}
                          </a>
                        )}
                        <a
                          href={e.source_image}
                          target="_blank"
                          rel="noreferrer"
                        >
                          目录原图 · PDF 第 {e.source_page} 页
                        </a>
                        {readingOnly && (
                          <a
                            href={`#/books/${bookId}?tab=review&task=navigation&entry=${encodeURIComponent(e.id)}`}
                          >
                            核对这条目录
                          </a>
                        )}
                        {!readingOnly && e.status !== "waiting_evidence" && (
                          <Button
                            variant="outline"
                            disabled={decision.isPending}
                            onClick={() => choose(e.id)}
                          >
                            {e.needs_confirmation ? "确认目录跳转" : "修改跳转"}
                          </Button>
                        )}
                      </div>
                    </>
                  )}
                  {activeId === e.id && (
                    <div ref={editor}>
                      <NavigationReview
                        key={`${e.id}:${restart}`}
                        entry={e}
                        report={report.data!}
                        runId={runId}
                        busy={decision.isPending}
                        approvalReady={runtimeScope !== "unknown"}
                        error={decision.error?.message}
                        reloading={report.isFetching}
                        onReload={async () => {
                          const result = await report.refetch();
                          if (result.isSuccess) {
                            decision.reset();
                            setRestart((v) => v + 1);
                          }
                        }}
                        onDecide={(choice, sha) =>
                          decision.mutate({ choice, sha, id: e.id })
                        }
                        onCancel={() => {
                          if (reviewOnly)
                            window.location.hash = `/books/${bookId}?tab=reader&section=directory`;
                          else setSelected(null);
                        }}
                      />
                    </div>
                  )}
                </li>
              ))}
            </ol>
          </div>
          {!report.data.coverage_complete && visible.length > 0 && (
            <p className="navigation-queue-status">
              {incompleteMessage} 标为“等待系统核验”的条目暂无需人工处理。
            </p>
          )}
        </>
      )}
    </section>
  );
}
