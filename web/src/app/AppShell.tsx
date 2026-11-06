import { lazy, Suspense, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { BookOpen, LibraryBig, ClipboardCheck, CloudCheck } from "lucide-react";
import { useWorkspaceEvents } from "@/hooks/useWorkspaceEvents.ts";
import { useQuery } from "@tanstack/react-query";
import { client } from "@/client/api.ts";
import { RuntimeScopeContext, type RuntimeScope } from "./runtime-scope.ts";
const UploadPanel = lazy(() => import("@/features/uploads/UploadPanel.tsx"));

export type WorkspaceContext = {
  upload: () => void;
  filter: string;
  setFilter: (value: string) => void;
  search: string;
  setSearch: (value: string) => void;
};

export default function AppShell() {
  const location = useLocation();
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
  const [open, setOpen] = useState(false);
  const [started, setStarted] = useState(false);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
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
            研究工作台 / {location.pathname === "/reviews" ? "待办" : "书籍"}
          </span>
          <span className="platform-sync">
            <CloudCheck size={16} />
            {connected ? "状态实时同步" : "正在重新连接"}
          </span>
        </header>
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
          <UploadPanel open={open} onOpenChange={setOpen} />
        </Suspense>
      )}
    </div>
  );
}
