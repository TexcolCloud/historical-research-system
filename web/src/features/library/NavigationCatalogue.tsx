import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import type { NavigationEntry, NavigationReport } from "./navigation-query";
import { readingHref } from "./reader-location";
import {
  navigationCompleted as linked,
  navigationStatusLabel,
} from "./navigation-status";

export type NavigationFilter = "attention" | "linked" | "waiting" | "all";

export function navigationIncompleteMessage(bookState?: string) {
  return bookState === "paused"
    ? "自动处理已暂停。请在处理进展中继续处理，新的目录项目将在核对后列出。"
    : bookState === "failed"
      ? "自动处理已停止。请在处理进展中从断点恢复，新的目录项目将在核对后列出。"
      : ["running", "processing", "queued"].includes(bookState ?? "")
        ? "目录尚未全部核对，新发现的项目会自动列出。"
        : "目录尚未全部核对，请在处理进展中查看剩余页面的状态。";
}

export function filterNavigationEntries(
  entries: NavigationEntry[],
  filter: NavigationFilter,
) {
  return entries.filter((entry) =>
    filter === "attention"
      ? entry.needs_confirmation
      : filter === "linked"
        ? linked(entry)
        : filter === "waiting"
          ? !linked(entry) && !entry.needs_confirmation
          : true,
  );
}

export function NavigationCoverage({ report }: { report: NavigationReport }) {
  return (
    <>
      {Boolean(
        report.missing_pages?.length || report.unresolved_pages?.length,
      ) && (
        <aside
          role="status"
          aria-label="目录覆盖检查"
          className="navigation-difference"
        >
          <strong>目录尚未补全</strong>
          <p>
            仍有原页的目录结构或条目待补全。当前清单只统计已发现的条目，已有关联可继续使用。
          </p>
          <ul>
            {[
              ...new Set([
                ...(report.missing_pages ?? []),
                ...(report.unresolved_pages ?? []),
              ]),
            ].map((page) => {
              const image = report.approved_pages?.find(
                (value) => value.page === page,
              )?.image;
              return (
                <li key={page}>
                  {image ? (
                    <a href={image} target="_blank" rel="noreferrer">
                      查看 PDF 第 {page} 页原图
                    </a>
                  ) : (
                    `PDF 第 ${page} 页`
                  )}
                </li>
              );
            })}
          </ul>
        </aside>
      )}
    </>
  );
}

export function NavigationFilters({
  entries,
  filter,
  onChange,
  disabled = false,
}: {
  entries: NavigationEntry[];
  filter: NavigationFilter;
  onChange: (value: NavigationFilter) => void;
  disabled?: boolean;
}) {
  const attention = entries.filter((entry) => entry.needs_confirmation);
  return (
    <div className="workspace-section-tabs" role="group" aria-label="目录状态">
      {(
        [
          ["attention", `需你核对 · ${attention.length}`],
          ["linked", `已设置或确认 · ${entries.filter(linked).length}`],
          [
            "waiting",
            `等待核验 · ${entries.filter((e) => !linked(e) && !e.needs_confirmation).length}`,
          ],
          ["all", `全部目录 · ${entries.length}`],
        ] as const
      ).map(([value, label]) => (
        <Button
          key={value}
          disabled={disabled}
          variant={filter === value ? "default" : "outline"}
          onClick={() => {
            onChange(value);
          }}
        >
          {label}
        </Button>
      ))}
    </div>
  );
}

export function NavigationEntrySummary({
  entry,
  bookId,
  action,
}: {
  entry: NavigationEntry;
  bookId: string;
  action: ReactNode;
}) {
  return (
    <>
      <header>
        <div>
          <strong>{entry.title}</strong>
          <p>
            书内页码 {entry.target_label}
            {entry.target_end_label ? `–${entry.target_end_label}` : ""}
          </p>
        </div>
        <span className="workspace-status-tag">
          {navigationStatusLabel(entry)}
        </span>
      </header>
      <div className="navigation-entry-links">
        {entry.target && (
          <a
            href={
              entry.reading_target
                ? `#${readingHref(bookId, entry.reading_target, { returnTo: `/books/${bookId}?tab=reader&section=directory&tocEntry=${encodeURIComponent(entry.id)}` })}`
                : (entry.target_image ?? entry.target_url ?? undefined)
            }
            target={entry.reading_target ? undefined : "_blank"}
            rel="noreferrer"
          >
            {entry.reading_target
              ? "阅读正文 →"
              : `查看正文原图 · PDF 第 ${entry.target.physical_page} 页`}
          </a>
        )}
        <a href={entry.source_image} target="_blank" rel="noreferrer">
          目录原图 · PDF 第 {entry.source_page} 页
        </a>
        {action}
      </div>
    </>
  );
}
