import { lazy, Suspense } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import { client, type Book } from "./client";
import { booksKey } from "./events";
import RunDetails from "./RunDetails";
import DeleteBookButton from "./DeleteBookButton";
const ReaderPanel = lazy(() => import("./ReaderPanel"));
const ReviewPanel = lazy(() => import("./ReviewPanel"));
const ExecutionPanel = lazy(() => import("./ExecutionPanel"));
const CardsPanel = lazy(() => import("./CardsPanel"));
const SearchPanel = lazy(() => import("./SearchPanel"));
export default function BookPage() {
  const { bookId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const cache = useQueryClient();
  const detail = useQuery({
    queryKey: ["platform", "book", bookId],
    initialData: () =>
      cache.getQueryData<Book[]>(booksKey)?.find((book) => book.id === bookId),
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/books/{book_id}", {
        signal,
        params: { path: { book_id: bookId } },
      });
      if (!data) throw new Error("暂时无法读取这本书，请重试。");
      return data;
    },
  });
  const book = detail.data;
  const tab =
    params.get("tab") ||
    (book?.state === "ready"
      ? "reader"
      : book?.state === "awaiting_review"
        ? "review"
        : "progress");
  if (detail.isPending) return <p role="status">正在读取书籍…</p>;
  if (!book)
    return (
      <p role="alert">
        {detail.error?.message ?? "书籍不存在。"} <Link to="/">返回书籍</Link>
      </p>
    );
  if (["deleting", "delete_failed", "deleted"].includes(book.state))
    return (
      <section className="platform-empty">
        <h1>
          {book.state === "deleted"
            ? "任务已删除"
            : book.state === "delete_failed"
              ? "删除未完成"
              : "正在删除任务"}
        </h1>
        <p role="status">
          {book.state === "deleted"
            ? "文件和任务结果已清理。"
            : book.state === "delete_failed"
              ? "部分清理尚未完成，可以重试继续清理。"
              : "正在停止执行并清理文件，完成后会自动从列表移除。"}
        </p>
        {book.state === "delete_failed" && <DeleteBookButton book={book} />}
        <Link to="/">返回全部书籍</Link>
      </section>
    );
  return (
    <>
      <Link to="/" className="platform-back">
        ← 全部书籍
      </Link>
      <div className="platform-heading">
        <div>
          <h1>{book.title}</h1>
          <p>以章节阅读，以原件核对，以证据研究。</p>
        </div>
        <DeleteBookButton book={book} />
      </div>
      <Tabs value={tab} onValueChange={(value) => setParams({ tab: value })}>
        <TabsList aria-label="书籍工作区">
          <TabsTrigger value="progress">处理进展</TabsTrigger>
          <TabsTrigger value="reader">阅读全文</TabsTrigger>
          <TabsTrigger value="review">内容核对</TabsTrigger>
          <TabsTrigger value="cards">史料卡</TabsTrigger>
          <TabsTrigger value="search">检索本书</TabsTrigger>
          <TabsTrigger value="execution">任务运行</TabsTrigger>
        </TabsList>
      </Tabs>
      <Suspense fallback={<p role="status">正在打开工作区…</p>}>
        {tab === "reader" ? (
          <ReaderPanel bookId={bookId} />
        ) : tab === "review" && book.run_id ? (
          <ReviewPanel
            runId={book.run_id}
            stage={book.stage ?? undefined}
            state={book.state}
          />
        ) : tab === "cards" ? (
          <CardsPanel bookId={bookId} published={book.state === "ready"} />
        ) : tab === "search" ? (
          <SearchPanel bookId={bookId} />
        ) : tab === "execution" ? (
          <ExecutionPanel bookId={bookId} />
        ) : (
          <RunDetails book={book} />
        )}
      </Suspense>
    </>
  );
}
