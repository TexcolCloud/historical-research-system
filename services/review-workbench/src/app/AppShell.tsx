import { lazy, Suspense, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import {
  BookOpen,
  LibraryBig,
  ClipboardCheck,
  NotebookPen,
  CloudCheck,
} from "lucide-react";
import { useWorkspaceEvents } from "@/hooks/useWorkspaceEvents.ts";
const UploadPanel = lazy(() => import("@/features/uploads/UploadPanel.tsx"));

export default function AppShell() {
  const location = useLocation();
  const connected = useWorkspaceEvents();
  const [open, setOpen] = useState(false);
  const [started, setStarted] = useState(false);
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
          <Outlet context={{ upload }} />
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
