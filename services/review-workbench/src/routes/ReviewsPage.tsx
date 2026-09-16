import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowUpRight, ClipboardCheck } from "lucide-react";
import { Button } from "@/components/ui/button.tsx";
import { readBooks } from "@/client/api.ts";
import { booksKey } from "@/hooks/events.ts";

export default function ReviewsPage() {
  const books = useQuery({
    queryKey: booksKey,
    queryFn: ({ signal }) => readBooks(signal),
  });
  const pending =
    books.data?.filter((book) =>
      ["awaiting_review", "failed"].includes(book.state),
    ) ?? [];
  return (
    <>
      <div className="platform-heading">
        <div>
          <p className="platform-eyebrow">需要你关注的内容</p>
          <h1>待办</h1>
          <p>
            内容核对和处理异常按书籍集中显示，已通过的部分不会重复要求确认。
          </p>
        </div>
      </div>
      {books.isError ? (
        <div role="alert">
          <p>{books.error.message}</p>
          <Button onClick={() => void books.refetch()}>重试</Button>
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
            <Link
              className="platform-collection-row"
              key={book.id}
              to={`/books/${book.id}?tab=${book.state === "failed" ? "progress" : "review"}`}
            >
              <ClipboardCheck size={24} />
              <div>
                <h2>{book.title}</h2>
                <p>
                  {book.state === "failed"
                    ? "处理未完成 · 查看原因并恢复"
                    : "需要人工核对 · 对照原件逐项处理"}
                </p>
              </div>
              <ArrowUpRight size={18} />
            </Link>
          ))}
        </section>
      )}
    </>
  );
}
