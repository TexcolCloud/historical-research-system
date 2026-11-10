import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { readingNavigationOptions } from "./navigation-query";
import {
  NavigationCoverage,
  NavigationEntrySummary,
  NavigationFilters,
  filterNavigationEntries,
  navigationIncompleteMessage,
  type NavigationFilter,
} from "./NavigationCatalogue";

/** Published navigation reads library projections and never submits decisions. */
export default function NavigationReader({
  bookId,
  bookState,
}: {
  bookId: string;
  bookState?: string;
}) {
  const report = useQuery(readingNavigationOptions(bookId));
  const [filter, setFilter] = useState<NavigationFilter>("all");
  const list = useRef<HTMLOListElement>(null);
  const entries = report.data?.entries ?? [];
  const visible = filterNavigationEntries(entries, filter);
  useEffect(() => {
    const requested = new URLSearchParams(
      window.location.hash.split("?")[1] ?? "",
    ).get("tocEntry");
    if (requested)
      list.current
        ?.querySelector<HTMLElement>(
          `[data-toc-entry="${CSS.escape(requested)}"]`,
        )
        ?.scrollIntoView?.({ block: "center" });
  }, [report.data]);
  return (
    <section className="platform-navigation" data-review-only={false}>
      <h2>原书目录</h2>
      <p>
        目录与当前阅读版保持一致。
        <a href={`#/books/${bookId}?tab=review&task=navigation`}>
          核对目录对应位置
        </a>
      </p>
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
          <NavigationFilters
            entries={entries}
            filter={filter}
            onChange={setFilter}
          />
          {!visible.length && (
            <div className="workspace-empty">
              <h3>
                {report.data.missing_pages?.length ||
                report.data.unresolved_pages?.length
                  ? "目录正在补全"
                  : filter === "attention"
                    ? "当前没有需要核对的目录"
                    : "暂无目录条目"}
              </h3>
              <p>
                {report.data.coverage_complete
                  ? "已确认的跳转可以在原书目录中试用或修改。"
                  : navigationIncompleteMessage(bookState)}
              </p>
              <a href={`#/books/${bookId}?tab=reader&section=directory`}>
                浏览原书目录
              </a>
            </div>
          )}
          <div>
            <ol
              ref={list}
              aria-label="原书目录"
              className="navigation-entry-list"
            >
              {visible.map((entry) => (
                <li
                  key={entry.id}
                  data-toc-entry={entry.id}
                  data-selected={false}
                >
                  <NavigationEntrySummary
                    entry={entry}
                    bookId={bookId}
                    action={
                      <a
                        href={`#/books/${bookId}?tab=review&task=navigation&entry=${encodeURIComponent(entry.id)}`}
                      >
                        核对这条目录
                      </a>
                    }
                  />
                </li>
              ))}
            </ol>
          </div>
          {!report.data.coverage_complete && visible.length > 0 && (
            <p className="navigation-queue-status">
              {navigationIncompleteMessage(bookState)}{" "}
              标为“等待系统核验”的条目暂无需人工处理。
            </p>
          )}
        </>
      )}
    </section>
  );
}
