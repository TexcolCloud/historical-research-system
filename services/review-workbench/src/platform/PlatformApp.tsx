import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  Upload,
  Search,
  LibraryBig,
  ArrowUpRight,
  CloudCheck,
  ClipboardCheck,
  NotebookPen,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import {
  Link,
  NavLink,
  Route,
  Routes,
  useNavigate,
  useLocation,
} from "react-router-dom";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import { readBooks } from "./client.ts";
import { applyBookEvent, booksKey } from "./events.ts";
import DeleteBookButton from "./DeleteBookButton";

const UploadPanel = lazy(() => import("./UploadPanel.tsx"));
const BookPage = lazy(() => import("./BookPage"));
const ReviewInbox = lazy(() =>
  import("./WorkspaceViews").then((module) => ({
    default: module.ReviewInbox,
  })),
);
const CardCollection = lazy(() =>
  import("./WorkspaceViews").then((module) => ({
    default: module.CardCollection,
  })),
);
const states: Record<string, string> = {
  uploading: "正在上传",
  queued: "等待处理",
  processing: "正在处理",
  awaiting_review: "需要核对",
  ready_for_ingestion: "核验完成 · 等待入库",
  ready: "已入库",
  failed: "处理未完成",
  deleting: "正在停止任务并清理文件",
  delete_failed: "删除未完成 · 可重试",
};

export default function PlatformApp() {
  const query = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false),
    [started, setStarted] = useState(false),
    [filter, setFilter] = useState("all"),
    [search, setSearch] = useState("");
  const [connected, setConnected] = useState(true);
  const books = useQuery({
    queryKey: booksKey,
    queryFn: ({ signal }) => readBooks(signal),
    refetchInterval: connected ? false : 30000,
  });
  useEffect(() => {
    const events = new EventSource("/api/v2/events");
    events.onopen = () => setConnected(true);
    events.onerror = () => setConnected(false);
    let cursor = 0;
    events.onmessage = (event) => {
      if (Number(event.lastEventId) <= cursor) return;
      cursor = applyBookEvent(query, event.data) ?? cursor;
    };
    return () => events.close();
  }, [query]);
  useEffect(() => {
    if (connected) return;
    const timer = window.setInterval(
      () =>
        void query.invalidateQueries({
          predicate: (entry) =>
            entry.queryKey[0] === "platform" &&
            [
              "run",
              "book",
              "runs",
              "issues",
              "executions",
              "cards",
              "chapters",
            ].includes(String(entry.queryKey[1])),
          refetchType: "active",
        }),
      30000,
    );
    return () => window.clearInterval(timer);
  }, [connected, query]);
  const visible = useMemo(
    () =>
      (books.data ?? []).filter(
        (book) =>
          book.title.toLowerCase().includes(search.toLowerCase()) &&
          (filter === "all" ||
            (filter === "review"
              ? book.state === "awaiting_review"
              : ["uploading", "queued", "processing"].includes(book.state))),
      ),
    [books.data, filter, search],
  );
  function upload() {
    setStarted(true);
    setOpen(true);
  }
  return (
    <div className="platform-workspace">
      <aside className="platform-sidebar">
        <a href="/platform.html" className="platform-brand">
          <LibraryBig />
          <span>
            史料研究<small>阅读 · 核对 · 研究</small>
          </span>
        </a>
        <nav aria-label="主要导航">
          <Link
            to="/"
            aria-current={
              location.pathname === "/" ||
              location.pathname.startsWith("/books/")
                ? "page"
                : undefined
            }
          >
            <BookOpen size={19} />
            书籍
          </Link>
          <NavLink to="/reviews">
            <ClipboardCheck size={19} />
            待办
          </NavLink>
          <NavLink to="/cards">
            <NotebookPen size={19} />
            史料卡
          </NavLink>
        </nav>
        <p className="platform-sidebar-note">
          从原始材料出发
          <br />
          让每一条认识都有出处
        </p>
      </aside>
      <div className="platform-body">
        <header className="platform-topbar">
          <span>
            研究工作台 /{" "}
            {location.pathname === "/reviews"
              ? "待办"
              : location.pathname === "/cards"
                ? "史料卡"
                : "书籍"}
          </span>
          <span className="platform-sync">
            <CloudCheck size={16} />
            {connected ? "状态实时同步" : "正在重新连接"}
          </span>
        </header>
        <main>
          <Routes>
            <Route
              path="/reviews"
              element={
                <Suspense fallback={<p role="status">正在打开待办…</p>}>
                  <ReviewInbox />
                </Suspense>
              }
            />
            <Route
              path="/cards"
              element={
                <Suspense fallback={<p role="status">正在打开史料卡…</p>}>
                  <CardCollection />
                </Suspense>
              }
            />
            <Route
              path="/books/:bookId"
              element={
                <Suspense fallback={<p role="status">正在打开书籍…</p>}>
                  <BookPage />
                </Suspense>
              }
            />
            <Route
              path="*"
              element={
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
                      <Button
                        variant="outline"
                        onClick={() => void books.refetch()}
                      >
                        重试
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
                        上传 PDF
                        后，系统会识别正文并核对原页。需要确认的内容会集中列出。
                      </p>
                      <Button onClick={upload}>导入 PDF</Button>
                    </section>
                  ) : (
                    <section className="platform-books" aria-label="书籍列表">
                      {visible.map((book) => (
                        <article key={book.id} className="platform-book-item">
                          <button
                            className="platform-book"
                            onClick={() => navigate(`/books/${book.id}`)}
                          >
                            <div
                              className="platform-book-cover"
                              aria-hidden="true"
                            >
                              <BookOpen size={27} />
                              <span>史料</span>
                            </div>
                            <div className="platform-book-copy">
                              <h2>{book.title}</h2>
                              <p>
                                {new Date(book.created_at).toLocaleDateString(
                                  "zh-CN",
                                )}{" "}
                                导入
                              </p>
                              <span
                                className="platform-status"
                                data-state={book.state}
                              >
                                {states[book.state] ?? book.state}
                              </span>
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
              }
            />
          </Routes>
        </main>
      </div>
      {started && (
        <Suspense
          fallback={open ? <p role="status">正在打开上传工具…</p> : null}
        >
          <UploadPanel open={open} onOpenChange={setOpen} />
        </Suspense>
      )}
    </div>
  );
}
