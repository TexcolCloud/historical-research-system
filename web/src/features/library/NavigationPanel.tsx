import { queryKeys } from "@/client/query-keys.ts";
import { useEffect, useRef, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
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
import { reviewPageOptions } from "@/features/review/review-page-query.ts";
import type { components } from "@/client/schema.d.ts";

function prefetchEntry(
  query: QueryClient,
  runId: string,
  entry?: components["schemas"]["NavigationEntry"],
) {
  if (!entry) return;
  const candidates = entry.target
    ? [entry.target, ...(entry.candidates ?? [])]
    : (entry.candidates ?? []);
  for (const page of new Set(
    candidates.slice(0, 3).map((candidate) => candidate.physical_page),
  ))
    void query.prefetchQuery({
      ...reviewPageOptions(runId, page),
      staleTime: 30_000,
    });
}
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
    [queueGroup, setQueueGroup] = useState("all"),
    [message, setMessage] = useState(""),
    [restart, setRestart] = useState(0);
  const entries = report.data?.entries ?? [],
    attention = entries.filter((e) => e.needs_confirmation);
  const hasCandidate = (entry: (typeof entries)[number]) =>
    Boolean(entry.target || entry.candidates?.length);
  const inGroup = (entry: (typeof entries)[number]) =>
    queueGroup === "recommended"
      ? hasCandidate(entry)
      : queueGroup === "search"
        ? !hasCandidate(entry)
        : true;
  const queueEntries = attention.filter(inGroup);
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
      (!reviewOnly ||
        (entry.needs_confirmation && inGroup(entry)) ||
        entry.id === entryId),
  );
  const activeId = readingOnly
    ? null
    : (selectedEntry?.id ?? (reviewOnly ? queueEntries[0]?.id : null));
  const nextEntry = queueEntries.find((entry) => entry.id !== activeId);
  useEffect(() => {
    if (reviewOnly) prefetchEntry(query, runId, nextEntry);
  }, [query, runId, reviewOnly, nextEntry]);
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
  function commitReport(data: components["schemas"]["NavigationReport"]) {
    const committed =
      query.setQueryData<components["schemas"]["NavigationReport"]>(
        queryKeys.navigation(runId),
        data,
      ) ?? data;
    updateBooks(
      query,
      (book) => book.id === bookId,
      (book) => ({
        ...book,
        navigation_pending_count: committed.pending_count,
      }),
    );
    void query.invalidateQueries({
      queryKey: queryKeys.readingNavigation(bookId),
    });
    return committed;
  }
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
      await query.cancelQueries({ queryKey: queryKeys.navigation(runId) });
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
    onSuccess: async (data, { choice, id }) => {
      await query.cancelQueries({ queryKey: queryKeys.navigation(runId) });
      const committed = commitReport(data);
      pendingDecision.current = null;
      const title = entries.find((e) => e.id === id)?.title ?? "这条目录";
      const automatic = committed.entries.filter(
        (entry) =>
          entry.id !== id &&
          ["matched", "matched_verified"].includes(entry.status) &&
          entries.some(
            (previous) =>
              previous.id === entry.id && previous.needs_confirmation,
          ),
      ).length;
      const group = entries.find((entry) => entry.id === id)?.group_id;
      const updated = group
        ? committed.entries.filter(
            (entry) =>
              entry.id !== id &&
              entry.needs_confirmation &&
              entry.group_id === group &&
              entries.some(
                (previous) =>
                  previous.id === entry.id &&
                  previous.needs_confirmation &&
                  JSON.stringify(previous.candidates) !==
                    JSON.stringify(entry.candidates),
              ),
          ).length
        : 0;
      setMessage(
        (choice
          ? `已确认：《${title}》 → PDF 第 ${choice.page} 页。`
          : `已确认：《${title}》保留目录文字，不设置跳转。`) +
          (automatic
            ? ` 另有 ${automatic} 条目录已由系统匹配，保留为机器关联。`
            : "") +
          (updated ? ` 同段另有 ${updated} 条目录候选已更新。` : ""),
      );
      setSelected(
        reviewOnly
          ? (committed.entries.find((e) => e.needs_confirmation && inGroup(e))
              ?.id ?? null)
          : null,
      );
      // The next bridge event replaces these committed local counters after projection.
    },
  });
  const recommendation = useMutation({
    mutationFn: async () => {
      if (runtimeScope === "unknown")
        throw new Error("工作区范围未确认，暂不能更新目录推荐。");
      await query.cancelQueries({ queryKey: queryKeys.navigation(runId) });
      const { data, response } = await client.POST(
        "/api/v2/runs/{run_id}/navigation/refresh",
        {
          params: { path: { run_id: runId } },
        },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "当前任务暂不能更新推荐，请在处理进展中核对任务状态后重试。"
            : "推荐更新未完成，已有目录与选择均已保留，请重试。",
        );
      return data;
    },
    onSuccess: async (data) => {
      await query.cancelQueries({ queryKey: queryKeys.navigation(runId) });
      const committed = commitReport(data);
      setMessage(
        `目录推荐已更新，当前 ${committed.pending_count} 项需要你核对。此次更新未提交人工决定。` +
          (committed.assistance_warning
            ? ` ${committed.assistance_warning}`
            : ""),
      );
    },
  });
  const busy = decision.isPending || recommendation.isPending;
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
          {reviewOnly && queueEntries.length > 0
            ? " 已打开下一项目录。"
            : reviewOnly && attention.length > 0
              ? " 当前组已完成，可切换其他分组。"
              : ""}
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
          {reviewOnly && (
            <div className="navigation-recommendation-tools">
              <p>
                先使用系统推荐，再核对仍有歧义的条目。更新推荐不会代替你的人工确认。
              </p>
              <Button
                variant="outline"
                disabled={busy || runtimeScope === "unknown"}
                onClick={() => recommendation.mutate()}
              >
                {recommendation.isPending ? "正在更新推荐…" : "更新推荐"}
              </Button>
              {recommendation.isError && (
                <p role="alert">{recommendation.error.message}</p>
              )}
            </div>
          )}
          {reviewOnly ? (
            <div>
              <p className="navigation-queue-status">
                {attention.length
                  ? `还有 ${attention.length} 项目录需要核对。确认跳转只改变目录导航，正文与原件保留。`
                  : "当前没有需要你确认的目录项目。"}
              </p>
              {attention.length > 0 && (
                <div
                  className="navigation-task-groups"
                  role="group"
                  aria-label="目录待办分组"
                >
                  {[
                    ["all", `全部待办 · ${attention.length}`],
                    [
                      "recommended",
                      `有推荐候选 · ${attention.filter(hasCandidate).length}`,
                    ],
                    [
                      "search",
                      `需查找正文 · ${attention.filter((entry) => !hasCandidate(entry)).length}`,
                    ],
                  ].map(([value, label]) => (
                    <Button
                      key={value}
                      variant={queueGroup === value ? "default" : "outline"}
                      aria-pressed={queueGroup === value}
                      disabled={busy}
                      onClick={() => {
                        setQueueGroup(value);
                        setSelected(null);
                        decision.reset();
                      }}
                    >
                      {label}
                    </Button>
                  ))}
                </div>
              )}
            </div>
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
                  disabled={busy}
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
                {reviewOnly && attention.length > 0
                  ? "当前分组没有待核对目录"
                  : reviewOnly && message
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
                  <p>
                    当前组 {queueEntries.length} 项 · 共 {attention.length} 项
                  </p>
                </header>
                <ol>
                  {(reviewOnly ? queueEntries : entries)
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
                          disabled={busy}
                          onPointerEnter={() =>
                            prefetchEntry(query, runId, entry)
                          }
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
                            disabled={busy}
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
                        busy={busy}
                        approvalReady={runtimeScope !== "unknown"}
                        error={decision.error?.message ?? report.error?.message}
                        reloading={report.isFetching}
                        onReload={async () => {
                          const result = await report.refetch();
                          if (!result.isSuccess) throw result.error;
                          decision.reset();
                          return result.data;
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
