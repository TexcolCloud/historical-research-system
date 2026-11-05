import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useOutletContext } from "react-router-dom";
import { BookOpen, Upload, Search, ArrowUpRight } from "lucide-react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs.tsx";
import { readBooks } from "@/client/api.ts";
import { booksKey } from "@/hooks/events.ts";
import DeleteBookButton from "@/features/books/DeleteBookButton.tsx";
import { bookWorkspaceHref } from "@/features/books/workspace.ts";
import type { WorkspaceContext } from "@/app/AppShell.tsx";
const states: Record<string, string> = {
  uploading: "正在上传",
  queued: "等待处理",
  processing: "正在处理",
  running: "正在处理",
  paused: "已暂停",
  awaiting_review: "需要核对",
  ready_for_ingestion: "核验完成 · 等待入库",
  ready: "已入库",
  failed: "处理未完成",
  deleting: "正在停止任务并清理文件",
  delete_failed: "删除未完成 · 可重试",
};

export default function BooksPage() {
  const navigate = useNavigate();
  const { upload, filter, setFilter, search, setSearch } =
    useOutletContext<WorkspaceContext>();
  const books = useQuery({
    queryKey: booksKey,
    queryFn: ({ signal }) => readBooks(signal),
  });
  const visible = useMemo(
    () =>
      (books.data ?? []).filter(
        (book) =>
          book.title.toLowerCase().includes(search.toLowerCase()) &&
          (filter === "all" ||
            (filter === "review"
              ? book.state === "awaiting_review" ||
                (book.pending_count ?? 0) > 0 ||
                (book.navigation_pending_count ?? 0) > 0
              : [
                  "uploading",
                  "queued",
                  "processing",
                  "running",
                  "paused",
                ].includes(book.state))),
      ),
    [books.data, filter, search],
  );
  return (
    <>
      <div className="platform-heading">
        <div>
          <p className="platform-eyebrow">你的研究，从这里开始</p>
          <h1>书籍</h1>
          <p>导入原件，核对内容，整理成可以阅读与研究的材料。</p>
        </div>
        <Button onClick={upload}>
          <Upload size={17} />
          导入书籍
        </Button>
      </div>
      <div className="platform-toolbar">
        <Tabs value={filter} onValueChange={setFilter}>
          <TabsList aria-label="书籍状态">
            <TabsTrigger value="all">全部书籍</TabsTrigger>
            <TabsTrigger value="processing">处理中</TabsTrigger>
            <TabsTrigger value="review">需要核对</TabsTrigger>
          </TabsList>
        </Tabs>
        <label className="platform-search">
          <Search size={17} />
          <Input
            aria-label="搜索书籍"
            placeholder="搜索书名…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
      </div>
      {books.isPending ? (
        <p role="status">正在读取书籍…</p>
      ) : books.isError ? (
        <div role="alert" className="platform-empty">
          <p>{books.error.message}</p>
          <Button variant="outline" onClick={() => void books.refetch()}>
            重新读取书籍
          </Button>
        </div>
      ) : !visible.length ? (
        <section className="platform-empty">
          <BookOpen size={36} />
          <h2>
            {search || filter !== "all"
              ? "没有符合条件的书籍"
              : "建立你的第一本研究材料"}
          </h2>
          <p>
            {search || filter !== "all"
              ? "可清空书名或切换到全部书籍。"
              : "上传 PDF 后自动提取文字并核对原图，需要你确认的疑点会集中列出。"}
          </p>
          {search || filter !== "all" ? (
            <Button
              onClick={() => {
                setSearch("");
                setFilter("all");
              }}
            >
              查看全部书籍
            </Button>
          ) : (
            <Button onClick={upload}>导入 PDF</Button>
          )}
        </section>
      ) : (
        <section className="platform-books" aria-label="书籍列表">
          {visible.map((book) => (
            <article key={book.id} className="platform-book-item">
              <button
                className="platform-book"
                onClick={() => navigate(bookWorkspaceHref(book))}
              >
                <div className="platform-book-cover" aria-hidden="true">
                  <BookOpen size={27} />
                  <span>史料</span>
                </div>
                <div className="platform-book-copy">
                  <h2>{book.title}</h2>
                  <p>
                    {new Date(book.created_at).toLocaleDateString("zh-CN")} 导入
                  </p>
                  <span className="platform-status" data-state={book.state}>
                    {states[book.state] ?? book.state}
                  </span>
                  {(book.pending_count ?? 0) > 0 && (
                    <p>{book.pending_count} 页待核对</p>
                  )}
                  {(book.navigation_pending_count ?? 0) > 0 && (
                    <p>{book.navigation_pending_count} 项目录位置待确认</p>
                  )}
                </div>
                <ArrowUpRight size={18} />
              </button>
              <div className="platform-book-actions">
                <DeleteBookButton book={book} />
              </div>
            </article>
          ))}
        </section>
      )}
    </>
  );
}
