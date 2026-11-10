import { Link } from "react-router-dom";
import { PanelLeftClose } from "lucide-react";
import { Button } from "@/components/ui/button";
import { readingHref, type ReadingTarget } from "./reader-location";
import { navigationStatusLabel } from "./navigation-status";
import type { ReaderData } from "./useReaderData";

type NavigationData = Pick<
  ReaderData,
  "navigation" | "chapters" | "id" | "prefetchChapter"
>;
export default function ReaderNavigation({
  bookId,
  open,
  view,
  onClose,
  onViewChange,
  onSelect,
  target,
  data,
}: {
  bookId: string;
  open: boolean;
  view: "original" | "chapters";
  onClose: () => void;
  onViewChange: (view: "original" | "chapters") => void;
  onSelect: (id: string) => void;
  target: ReadingTarget | null;
  data: NavigationData;
}) {
  const { navigation, chapters, id, prefetchChapter } = data;
  return (
    <nav aria-label="章节目录" hidden={!open}>
      <div className="reader-navigation-heading">
        <h2>本书导航</h2>
        <Button
          variant="ghost"
          size="icon"
          aria-label="关闭导航"
          onClick={onClose}
        >
          <PanelLeftClose size={18} />
        </Button>
      </div>
      <div
        className="reader-navigation-tabs"
        role="group"
        aria-label="导航方式"
      >
        <Button
          variant={view === "original" ? "default" : "ghost"}
          aria-pressed={view === "original"}
          onClick={() => onViewChange("original")}
        >
          原书目录
        </Button>
        <Button
          variant={view === "chapters" ? "default" : "ghost"}
          aria-pressed={view === "chapters"}
          onClick={() => onViewChange("chapters")}
        >
          章节
        </Button>
      </div>
      <div hidden={view !== "original"}>
        {navigation.isPending && <p role="status">正在读取原书目录…</p>}
        {navigation.isError && (
          <p role="alert">
            {navigation.error.message}
            <Button variant="outline" onClick={() => void navigation.refetch()}>
              重新读取目录
            </Button>
          </p>
        )}
        <ol className="reader-source-toc">
          {(navigation.data?.entries ?? []).map((entry) => (
            <li
              key={entry.id}
              style={{
                paddingInlineStart: `${Math.max(0, entry.level - 1) * 0.75}rem`,
              }}
            >
              {entry.reading_target && !entry.needs_confirmation ? (
                <Link
                  to={readingHref(bookId, entry.reading_target, {
                    returnTo: `/books/${bookId}?tab=reader&section=directory&tocEntry=${encodeURIComponent(entry.id)}`,
                  })}
                  aria-current={
                    target?.chapter_id === entry.reading_target.chapter_id &&
                    target.start === entry.reading_target.start
                      ? "location"
                      : undefined
                  }
                >
                  {entry.title}
                  <small>书内页 {entry.target_label}</small>
                </Link>
              ) : (
                <>
                  <span>
                    {entry.title}
                    <small>
                      {entry.target_label} · {navigationStatusLabel(entry)}
                    </small>
                  </span>
                  {entry.needs_confirmation && (
                    <Link
                      className="reader-toc-management"
                      to={`/books/${bookId}?tab=review&task=navigation&entry=${encodeURIComponent(entry.id)}`}
                    >
                      核对位置
                    </Link>
                  )}
                  {!entry.needs_confirmation &&
                    (entry.target_image || entry.target_url) && (
                      <a
                        className="reader-toc-management"
                        href={
                          entry.target_image ?? entry.target_url ?? undefined
                        }
                        target="_blank"
                        rel="noreferrer"
                      >
                        查看对应原图
                      </a>
                    )}
                </>
              )}
            </li>
          ))}
        </ol>
        {!navigation.isPending &&
          !navigation.isError &&
          !navigation.data?.entries.length && (
            <p className="reader-navigation-empty">
              本书没有可用的原书目录。可切换到章节阅读。
            </p>
          )}
      </div>
      <div className="reader-chapter-list" hidden={view !== "chapters"}>
        {chapters.data?.map((item) => (
          <button
            key={item.id}
            aria-current={item.id === id ? "true" : undefined}
            onPointerEnter={() => void prefetchChapter(item.id)}
            onClick={() => onSelect(item.id)}
          >
            {item.title}
            <small>
              原件 {item.pages[0]}–{item.pages.at(-1)} 页
            </small>
          </button>
        ))}
      </div>
      <Link
        className="reader-directory-link"
        to={`/books/${bookId}?tab=reader&section=directory${id ? `&chapter=${encodeURIComponent(id)}` : ""}`}
      >
        查看完整目录与核对状态
      </Link>
    </nav>
  );
}
