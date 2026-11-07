import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowUpRight, ClipboardCheck, CircleAlert } from "lucide-react";
import { Button } from "@/components/ui/button.tsx";
import { readBooks } from "@/client/api.ts";
import { booksKey } from "@/hooks/events.ts";
import {
  bookWorkspaceHref,
  hasNavigationWork,
} from "@/features/books/workspace.ts";

export default function ReviewsPage() {
  const books = useQuery({
    queryKey: booksKey,
    queryFn: ({ signal }) => readBooks(signal),
  });
  const pending =
    books.data?.filter(
      (book) =>
        ["awaiting_review", "failed"].includes(book.state) ||
        (book.pending_count ?? 0) > 0 ||
        hasNavigationWork(book),
    ) ?? [];
  return (
    <>
      <div className="platform-heading">
        <div>
          <p className="platform-eyebrow">待你处理</p>
          <h1>待办</h1>
          <p>
            内容核对和处理异常按书籍集中显示，已通过的部分不会重复要求确认。
          </p>
        </div>
        {books.data && (
          <span className="platform-list-count">
            {pending.length} 本资料需要处理
          </span>
        )}
      </div>
      {books.isError ? (
        <div role="alert">
          <p>{books.error.message}</p>
          <Button onClick={() => void books.refetch()}>重新读取待办</Button>
        </div>
      ) : books.isPending ? (
        <p role="status">正在读取待办…</p>
      ) : !pending.length ? (
        <section className="platform-empty">
          <ClipboardCheck size={36} />
          <h2>暂时没有需要处理的内容</h2>
          <p>书籍核验发现问题后，会在这里列出。</p>
          <Link to="/">查看书籍进展</Link>
        </section>
      ) : (
        <section className="platform-collection" aria-label="按书籍整理的待办">
          {pending.map((book) => (
            <article
              className="platform-collection-row"
              key={book.id}
              data-state={book.state}
            >
              {book.state === "failed" ? (
                <CircleAlert size={24} />
              ) : (
                <ClipboardCheck size={24} />
              )}
              <div>
                <h2>
                  <Link to={bookWorkspaceHref(book)}>{book.title}</Link>
                </h2>
                <p>
                  {book.state === "failed"
                    ? "处理未完成 · 查看原因并恢复"
                    : (book.pending_count ?? 0) > 0
                      ? `${book.pending_count} 页待核对 · ${book.state === "paused" ? "自动处理已暂停" : book.state === "running" ? "其余页面继续自动处理" : book.state === "queued" ? "自动任务排队中" : "确认后继续后续处理"}`
                      : (book.navigation_pending_count ?? 0) > 0
                        ? `${book.navigation_pending_count} 项目录位置需确认 · 不影响已核验正文`
                        : (book.navigation_missing_page_count ?? 0) > 0
                          ? `${book.navigation_missing_page_count} 页目录待补全 · 查看原图和补全进展`
                          : "需要人工核对 · 对照原件逐项处理"}
                </p>
                <div className="workspace-task-links">
                  {(book.pending_count ?? 0) > 0 && (
                    <Link to={`/books/${book.id}?tab=review`}>
                      核对正文 · {book.pending_count} 页
                    </Link>
                  )}
                  {(book.navigation_pending_count ?? 0) > 0 && (
                    <Link to={`/books/${book.id}?tab=review&task=navigation`}>
                      确认目录 · {book.navigation_pending_count} 项
                    </Link>
                  )}
                  {(book.navigation_missing_page_count ?? 0) > 0 && (
                    <Link to={`/books/${book.id}?tab=review&task=navigation`}>
                      查看目录补全 · {book.navigation_missing_page_count} 页
                    </Link>
                  )}
                  {book.state === "failed" && (
                    <Link to={`/books/${book.id}?tab=progress`}>
                      查看异常并恢复
                    </Link>
                  )}
                </div>
              </div>
              <ArrowUpRight size={18} />
            </article>
          ))}
        </section>
      )}
    </>
  );
}
