import { lazy, Suspense, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs.tsx";
import { client, type Book } from "@/client/api.ts";
import { booksKey } from "@/hooks/events.ts";
import RunDetails from "@/features/runs/RunDetails.tsx";
import DeleteBookButton from "@/features/books/DeleteBookButton.tsx";
import { bookWorkspaceTab } from "@/features/books/workspace.ts";
const ReadingWorkspace = lazy(
  () => import("@/features/library/ReadingWorkspace.tsx"),
);
const ReviewPanel = lazy(() => import("@/features/review/ReviewPanel.tsx"));
const ExecutionPanel = lazy(
  () => import("@/features/execution/ExecutionPanel.tsx"),
);
const NavigationPanel = lazy(
  () => import("@/features/library/NavigationPanel.tsx"),
);
export default function BookPage() {
  const { bookId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const [diagnostics, setDiagnostics] = useState(
    params.get("tab") === "execution",
  );
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
  const requested = params.get("tab");
  const directoryTask =
    params.get("task") === "navigation" ||
    (!params.get("task") &&
      !book?.pending_count &&
      (book?.navigation_pending_count ?? 0) > 0);
  const tab = ["navigation", "search"].includes(requested ?? "")
    ? "reader"
    : requested === "execution"
      ? "progress"
      : requested || (book ? bookWorkspaceTab(book) : "progress");
  function chooseTab(value: string) {
    const next = new URLSearchParams(params);
    next.set("tab", value);
    setParams(next);
  }
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
      <div className="platform-heading book-heading">
        <div>
          <h1 title={book.title}>{book.title}</h1>
          <p>
            {book.state === "failed"
              ? "自动处理已停止，请在处理进展中从断点恢复。"
              : book.state === "paused"
                ? "自动处理已暂停，请在处理进展中点击“继续处理”。"
                : (book.pending_count ?? 0) > 0 ||
                    (book.navigation_pending_count ?? 0) > 0
                  ? `需要你处理：${book.pending_count ?? 0} 页正文核对 · ${book.navigation_pending_count ?? 0} 项目录确认`
                  : book.state === "ready"
                    ? "资料已入库，可阅读、检索和导出。"
                    : "系统按页自动处理，已有底稿可在阅读资料中查看。"}
          </p>
          {book.revision_pending && (
            <p role="note">
              新版本正在复核，阅读和检索继续使用此前发布的版本。
            </p>
          )}
        </div>
        <DeleteBookButton book={book} />
      </div>
      <Tabs value={tab} onValueChange={chooseTab}>
        <TabsList aria-label="书籍工作区" className="workspace-main-tabs">
          <TabsTrigger value="progress">处理进展</TabsTrigger>
          <TabsTrigger value="review">人工核对</TabsTrigger>
          <TabsTrigger value="reader">阅读资料</TabsTrigger>
        </TabsList>
      </Tabs>
      <Suspense fallback={<p role="status">正在打开工作区…</p>}>
        {tab === "reader" ? (
          <ReadingWorkspace book={book} />
        ) : tab === "review" && book.run_id ? (
          <section className="human-workspace">
            <div
              className="workspace-section-tabs"
              role="group"
              aria-label="核对任务类型"
            >
              <Button
                variant={directoryTask ? "outline" : "default"}
                onClick={() => {
                  const next = new URLSearchParams(params);
                  next.set("task", "content");
                  next.set("tab", "review");
                  setParams(next);
                }}
              >
                正文核对 · {book.pending_count ?? 0} 页
              </Button>
              <Button
                variant={directoryTask ? "default" : "outline"}
                onClick={() => {
                  const next = new URLSearchParams(params);
                  next.set("task", "navigation");
                  next.set("tab", "review");
                  setParams(next);
                }}
              >
                目录确认 · {book.navigation_pending_count ?? 0} 项
              </Button>
            </div>
            {directoryTask ? (
              <NavigationPanel
                runId={book.run_id}
                bookId={bookId}
                bookState={book.state}
                reviewOnly
                entryId={params.get("entry") ?? undefined}
              />
            ) : (
              <ReviewPanel
                bookId={bookId}
                runId={book.run_id}
                stage={book.stage ?? undefined}
                state={book.state}
              />
            )}
          </section>
        ) : (
          <>
            <RunDetails book={book} />
            <details
              className="workspace-diagnostics"
              open={diagnostics}
              onToggle={(e) => setDiagnostics(e.currentTarget.open)}
            >
              <summary>运行诊断与处理记录</summary>
              {diagnostics && <ExecutionPanel bookId={bookId} />}
            </details>
          </>
        )}
      </Suspense>
    </>
  );
}
