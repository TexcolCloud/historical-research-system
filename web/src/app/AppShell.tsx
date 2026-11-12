import { lazy, Suspense, useEffect, useState } from "react";
import { Link, Outlet, useLocation, useMatch } from "react-router-dom";
import {
  CloudCheck,
  Upload,
  PanelLeft,
  LayoutDashboard,
  Search,
  Library,
  FolderOpen,
  ClipboardCheck,
  Settings,
  ScrollText,
} from "lucide-react";
import { useWorkspaceEvents } from "@/hooks/useWorkspaceEvents.ts";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/client/api.ts";
import { RuntimeScopeContext, type RuntimeScope } from "./runtime-scope.ts";
const UploadPanel = lazy(() => import("@/features/uploads/UploadPanel.tsx"));
import type { UploadActivity } from "@/features/uploads/UploadPanel.tsx";
import { Button } from "@/components/ui/button.tsx";
import { studyRequest } from "@/features/studies/api";
import "@/styles/study-shell.css";
import type { LucideIcon } from "lucide-react";
import { useWorkOverview } from "@/features/studies/work-overview";
import { rememberResumeWork } from "./workspace-resume";

const sections: { group: string; links: [string, string, LucideIcon][] }[] = [
  {
    group: "工作空间",
    links: [
      ["继续工作", "/dashboard", LayoutDashboard],
      ["史料集", "/library", Library],
      ["全文检索", "/search", Search],
      ["研究专题", "/studies", FolderOpen],
      ["待办", "/reviews", ClipboardCheck],
    ],
  },
];

export type WorkspaceContext = {
  upload: () => void;
  filter: string;
  setFilter: (value: string) => void;
  search: string;
  setSearch: (value: string) => void;
};

export default function AppShell() {
  const location = useLocation();
  const query = useQueryClient();
  const canvasMatch = useMatch("/studies/:topicId/canvas");
  const bookMatch = useMatch("/books/:bookId");
  const canvas = !!canvasMatch;
  const bookMode = new URLSearchParams(location.search).get("tab") ?? "";
  const focused =
    canvas ||
    (location.pathname.startsWith("/books/") &&
      ["review", "reader"].includes(bookMode));
  const focusEntry = focused
    ? `${location.pathname}:${canvas ? "canvas" : bookMode}`
    : "normal";
  const [sidebarOpen, setSidebarOpen] = useState(!focused);
  useEffect(() => setSidebarOpen(!focused), [focused, focusEntry]);
  const preferences = useQuery({
    queryKey: ["studies", "preferences"],
    staleTime: 300000,
    retry: false,
    queryFn: ({ signal }) =>
      studyRequest<{ compact: boolean }>(
        "/preferences",
        "GET",
        undefined,
        signal,
      ),
  });
  const connected = useWorkspaceEvents();
  const runtime = useQuery({
    queryKey: ["platform", "runtime"],
    retry: false,
    queryFn: async () => {
      const { data } = await client.GET("/api/v2/runtime");
      if (
        !data ||
        !["content", "workflow_only"].includes(data.validation_scope)
      )
        throw new Error("工作区范围未确认，已保存的草稿和正文仍可查看。");
      return data;
    },
  });
  const scope: RuntimeScope =
    runtime.isError || !runtime.data
      ? "unknown"
      : runtime.data.validation_scope === "workflow_only"
        ? "workflow_only"
        : "content";
  const overview = useWorkOverview({
    enabled: !!runtime.data?.workspace_id,
    loadResearch: false,
  });
  const bookCountLabel =
    overview.pendingBookCount === undefined
      ? "资料待读取"
      : `${overview.pendingBookCount}本资料`;
  const researchCountLabel =
    overview.pendingResearchCount === undefined
      ? "研究待读取"
      : `${overview.truncated ? "至少" : ""}${overview.pendingResearchCount}项研究`;
  const topicId =
    canvasMatch?.params.topicId ||
    new URLSearchParams(location.search).get("topic");
  const currentBook = overview.books.data?.find(
    (book) => book.id === bookMatch?.params.bookId,
  );
  const resumeHref = location.pathname + location.search + location.hash;
  useEffect(() => {
    const workspace = runtime.data?.workspace_id;
    if (!workspace) return;
    const searchTerm = new URLSearchParams(location.search).get("q");
    const labels: Record<string, string> = {
      reader: "阅读",
      review: "内容核对",
      research: "史料卡",
      progress: "处理进展",
    };
    const title = currentBook
      ? `${labels[bookMode] || "书籍"}：${currentBook.title}`
      : location.pathname === "/search" && searchTerm
        ? `检索：${searchTerm.slice(0, 60)}`
        : "";
    if (!title) return;
    const resume = rememberResumeWork(workspace, resumeHref, title);
    if (resume) query.setQueryData(["platform", "resume", workspace], resume);
  }, [
    runtime.data?.workspace_id,
    resumeHref,
    currentBook?.title,
    bookMode,
    location.pathname,
    location.search,
    query,
  ]);
  const [open, setOpen] = useState(false);
  const [started, setStarted] = useState(false);
  const [activity, setActivity] = useState<UploadActivity>({
    total: 0,
    pending: 0,
    uploading: 0,
  });
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  function upload() {
    setStarted(true);
    setOpen(true);
  }
  return (
    <div
      className="platform-workspace study-shell"
      data-sidebar={sidebarOpen ? "open" : "closed"}
      data-density={preferences.data?.compact ? "compact" : "normal"}
      data-canvas={canvas}
    >
      <aside
        className="study-sidebar"
        aria-label="研究功能导航"
        hidden={!sidebarOpen}
      >
        <Link className="study-sidebar-brand" to="/dashboard">
          <span className="platform-seal">史</span>
          <span>
            史料研究<small>个人研究工作台</small>
          </span>
        </Link>
        <nav aria-label="主要导航">
          {sections.map((section) => (
            <div key={section.group}>
              <p>{section.group}</p>
              {section.links.map(([label, path, Glyph]) => {
                const active =
                  location.pathname === path ||
                  (path === "/library" &&
                    (location.pathname.startsWith("/books/") ||
                      location.pathname === "/research")) ||
                  (path === "/studies" &&
                    (canvas ||
                      [
                        "/arguments",
                        "/timeline",
                        "/analysis",
                        "/workspace",
                      ].includes(location.pathname)));
                return (
                  <Link
                    key={path}
                    to={
                      path === "/studies" && topicId
                        ? `/studies?topic=${encodeURIComponent(topicId)}`
                        : path
                    }
                    aria-label={label === "待办" ? "待办" : undefined}
                    aria-describedby={
                      label === "待办" ? "workspace-queue-count" : undefined
                    }
                    title={
                      label === "待办"
                        ? `${bookCountLabel} · ${researchCountLabel}`
                        : undefined
                    }
                    aria-current={active ? "page" : undefined}
                  >
                    <Glyph size={18} aria-hidden="true" />
                    {label}
                    {label === "待办" && (
                      <>
                        {" "}
                        <span
                          className="study-navigation-count"
                          id="workspace-queue-count"
                          aria-hidden="true"
                        >
                          <span>{bookCountLabel}</span>
                          <span>{researchCountLabel}</span>
                        </span>
                      </>
                    )}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>
        <nav className="study-sidebar-footer" aria-label="工作空间设置">
          <Link
            to="/settings"
            aria-current={
              location.pathname === "/settings" ? "page" : undefined
            }
          >
            <Settings size={16} aria-hidden="true" />
            设置
          </Link>
          <Link
            to="/updates"
            aria-current={location.pathname === "/updates" ? "page" : undefined}
          >
            <ScrollText size={16} aria-hidden="true" />
            更新日志
          </Link>
        </nav>
      </aside>
      <header className="platform-header">
        <Button
          variant="ghost"
          aria-label={sidebarOpen ? "收起功能导航" : "展开功能导航"}
          aria-expanded={sidebarOpen}
          onClick={() => setSidebarOpen(!sidebarOpen)}
        >
          <PanelLeft size={20} />
        </Button>
        <Link
          to="/dashboard"
          className="platform-brand"
          aria-label="史料研究 · 继续工作"
        >
          <span className="platform-seal" aria-hidden="true">
            史
          </span>
          <span>史料研究</span>
        </Link>
        <div className="platform-header-actions">
          <span className="platform-sync" data-connected={connected}>
            <CloudCheck size={16} />
            {connected ? "本地书库" : "正在重新连接"}
          </span>
          <Button
            variant="outline"
            onClick={upload}
            aria-label={started ? "查看导入进展" : "上传史料"}
          >
            <Upload size={16} />
            {activity.uploading
              ? `上传中 · ${activity.uploading}`
              : activity.pending
                ? `待上传 · ${activity.pending}`
                : "上传史料"}
          </Button>
        </div>
      </header>
      <div className="platform-body">
        <main>
          {scope === "workflow_only" && (
            <p role="note" className="platform-inline-status">
              流程验收工作区：本轮授权放行仅验证顺序和格式，不代表内容准确率通过。
            </p>
          )}
          {scope === "unknown" && (
            <p
              role={runtime.isError ? "alert" : "status"}
              className="platform-inline-status"
            >
              {runtime.isError
                ? "工作区范围未确认，可继续阅读、核对和保存草稿，确认范围后才能提交终审。"
                : "正在确认工作区范围，可继续阅读和保存草稿。"}
              {runtime.isError && (
                <button
                  type="button"
                  disabled={runtime.isFetching}
                  onClick={() => void runtime.refetch()}
                >
                  重新确认工作区
                </button>
              )}
            </p>
          )}
          <RuntimeScopeContext.Provider value={scope}>
            <Outlet
              context={
                {
                  upload,
                  filter,
                  setFilter,
                  search,
                  setSearch,
                } satisfies WorkspaceContext
              }
            />
          </RuntimeScopeContext.Provider>
        </main>
      </div>
      {started && (
        <Suspense
          fallback={open ? <p role="status">正在打开上传工具…</p> : null}
        >
          <UploadPanel
            open={open}
            onOpenChange={setOpen}
            onActivityChange={setActivity}
          />
        </Suspense>
      )}
    </div>
  );
}
