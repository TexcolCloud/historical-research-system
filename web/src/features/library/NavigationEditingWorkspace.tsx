import {
  NavigationCoverage,
  NavigationFilters,
  NavigationEntrySummary,
  filterNavigationEntries,
  navigationIncompleteMessage,
  type NavigationFilter,
} from "./NavigationCatalogue";
import { useNavigationActions } from "./useNavigationActions";
import { useEffect, useRef, useState } from "react";
import {
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { Button } from "@/components/ui/button.tsx";
import NavigationReview from "./NavigationReview.tsx";
import { navigationOptions } from "./navigation-query";
import { navigationStatusLabel } from "./navigation-status";
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
export default function NavigationEditingWorkspace({
  runId,
  bookId,
  bookState,
  reviewOnly = false,
  entryId,
}: {
  runId: string;
  bookId: string;
  bookState?: string;
  reviewOnly?: boolean;
  entryId?: string;
}) {
  const query = useQueryClient();
  const report = useQuery(navigationOptions(runId));
  const [selected, setSelected] = useState<string | null>(entryId ?? null),
    [filter, setFilter] = useState<NavigationFilter>(
      reviewOnly ? "attention" : "all",
    ),
    [queueGroup, setQueueGroup] = useState("all"),
    [message, setMessage] = useState<{
      kind: "decision" | "recommendation";
      text: string;
    } | null>(null),
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
  const incompleteMessage = navigationIncompleteMessage(bookState);
  const selectedEntry = entries.find(
    (entry) =>
      entry.id === selected &&
      (!reviewOnly ||
        (entry.needs_confirmation && inGroup(entry)) ||
        entry.id === entryId),
  );
  const activeId =
    selectedEntry?.id ?? (reviewOnly ? queueEntries[0]?.id : null);
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
  const { decision, recommendation, busy, approvalReady } =
    useNavigationActions({
      runId,
      bookId,
      onDecision(committed, choice, id) {
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
        setMessage({
          kind: "decision",
          text:
            (choice
              ? `已确认：《${title}》 → PDF 第 ${choice.page} 页。`
              : `已确认：《${title}》保留目录文字，不设置跳转。`) +
            (automatic
              ? ` 另有 ${automatic} 条目录已由系统匹配，保留为机器关联。`
              : "") +
            (updated ? ` 同段另有 ${updated} 条目录候选已更新。` : ""),
        });
        setSelected(
          reviewOnly
            ? (committed.entries.find((e) => e.needs_confirmation && inGroup(e))
                ?.id ?? null)
            : null,
        );
      },
      onRecommendation(committed) {
        setMessage({
          kind: "recommendation",
          text:
            "目录推荐已更新，剩余待办见下方清单。此次更新未提交人工决定。" +
            (committed.assistance_warning
              ? ` ${committed.assistance_warning}`
              : ""),
        });
      },
    });
  const visible = reviewOnly
    ? entries.filter((e) => e.id === activeId)
    : filterNavigationEntries(entries, filter);
  function choose(id: string) {
    setSelected(id);
    decision.reset();
    setRestart((v) => v + 1);
  }
  return (
    <section className="platform-navigation" data-review-only={reviewOnly}>
      {!reviewOnly && <h2>原书目录</h2>}
      {!reviewOnly && (
        <p>点击目录可阅读正文；跳转位置不对时，可在当前条目重新核对。</p>
      )}
      {message && (
        <p role="status" className="navigation-receipt">
          {message.text}
          {message.kind === "decision" && reviewOnly && queueEntries.length > 0
            ? " 已打开下一项目录。"
            : ""}
        </p>
      )}
      {reviewOnly && attention.length > 0 && queueEntries.length === 0 && (
        <p role="status" className="navigation-queue-status">
          当前组已完成，可切换其他分组。
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
          <NavigationCoverage report={report.data} />
          {reviewOnly && (
            <div className="navigation-recommendation-tools">
              <p>
                先使用系统推荐，再核对仍有歧义的条目。更新推荐不会代替你的人工确认。
              </p>
              <Button
                variant="outline"
                disabled={busy || !approvalReady}
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
                  : report.data.missing_pages?.length ||
                      report.data.unresolved_pages?.length
                    ? "已发现的目录条目均已处理，仍需补全上述目录页。"
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
            <NavigationFilters
              entries={entries}
              filter={filter}
              disabled={busy}
              onChange={(value) => {
                setFilter(value);
                setSelected(null);
                decision.reset();
              }}
            />
          )}
          {!visible.length && (
            <div className="workspace-empty">
              <h3>
                {report.data.missing_pages?.length ||
                report.data.unresolved_pages?.length
                  ? "目录正在补全"
                  : reviewOnly && attention.length > 0
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
                    <NavigationEntrySummary
                      entry={e}
                      bookId={bookId}
                      action={
                        e.status !== "waiting_evidence" ? (
                          <Button
                            variant="outline"
                            disabled={busy}
                            onClick={() => choose(e.id)}
                          >
                            {e.needs_confirmation ? "确认目录跳转" : "修改跳转"}
                          </Button>
                        ) : null
                      }
                    />
                  )}
                  {activeId === e.id && (
                    <div ref={editor}>
                      <NavigationReview
                        key={`${e.id}:${restart}`}
                        entry={e}
                        report={report.data!}
                        runId={runId}
                        busy={busy}
                        saving={decision.isPending}
                        approvalReady={approvalReady}
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
