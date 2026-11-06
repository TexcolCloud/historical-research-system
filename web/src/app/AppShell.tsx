import { lazy, Suspense, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { CloudCheck, Upload } from "lucide-react";
import { useWorkspaceEvents } from "@/hooks/useWorkspaceEvents.ts";
import { useQuery } from "@tanstack/react-query";
import { client } from "@/client/api.ts";
import { RuntimeScopeContext, type RuntimeScope } from "./runtime-scope.ts";
const UploadPanel = lazy(() => import("@/features/uploads/UploadPanel.tsx"));
import type { UploadActivity } from "@/features/uploads/UploadPanel.tsx";
import { Button } from "@/components/ui/button.tsx";

export type WorkspaceContext = {
  upload: () => void;
  filter: string;
  setFilter: (value: string) => void;
  search: string;
  setSearch: (value: string) => void;
};

export default function AppShell() {
  const location = useLocation();
  const reviewing =
    location.pathname === "/reviews" ||
    (location.pathname.startsWith("/books/") &&
      new URLSearchParams(location.search).get("tab") === "review");
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
  const [activity, setActivity] = useState<UploadActivity>({
    total: 0,
    pending: 0,
  });
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  function upload() {
    setStarted(true);
    setOpen(true);
  }
  return (
    <div className="platform-workspace">
      <header className="platform-header">
        <Link to="/" className="platform-brand" aria-label="史料研究 · 书籍">
          <span className="platform-seal" aria-hidden="true">
            史
          </span>
          <span>史料研究</span>
        </Link>
        <nav aria-label="主要导航">
          <Link to="/" aria-current={!reviewing ? "page" : undefined}>
            书籍
          </Link>
          <Link to="/reviews" aria-current={reviewing ? "page" : undefined}>
            待办
          </Link>
        </nav>
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
            {activity.pending ? `上传中 · ${activity.pending}` : "上传史料"}
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
