import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/client/api";
import type { components } from "@/client/schema";
import { Button } from "@/components/ui/button";
import { studyRequest } from "./api";

type Preferences = components["schemas"]["PreferencesResponse"];

export function SettingsPage() {
  const cache = useQueryClient();
  const preferences = useQuery({
    queryKey: ["studies", "preferences"],
    queryFn: ({ signal }) =>
      studyRequest<Preferences>("/preferences", "GET", undefined, signal),
  });
  const runtime = useQuery({
    queryKey: ["platform", "runtime"],
    queryFn: async () => {
      const { data } = await client.GET("/api/v2/runtime");
      if (!data) throw new Error("无法读取工作区。");
      return data;
    },
  });
  const save = useMutation({
    mutationFn: (changes: Partial<Preferences>) =>
      studyRequest<Preferences>("/preferences", "PATCH", {
        compact: changes.compact ?? preferences.data?.compact ?? false,
        restore_last_canvas:
          changes.restore_last_canvas ??
          preferences.data?.restore_last_canvas ??
          true,
        expected_version: preferences.data?.version ?? 0,
      }),
    onSuccess: (value) => cache.setQueryData(["studies", "preferences"], value),
  });
  return (
    <section className="study-page">
      <header className="study-page-heading">
        <div>
          <h1>设置</h1>
          <p>当前本机工作区与研究桌面偏好。</p>
        </div>
      </header>
      <section className="study-overview-card">
        <h2>运行环境</h2>
        {runtime.isError ? (
          <p role="alert">{runtime.error.message}</p>
        ) : (
          <dl className="study-settings-list">
            <dt>工作区</dt>
            <dd>{runtime.data?.workspace_id ?? "读取中…"}</dd>
            <dt>识别</dt>
            <dd>{runtime.data?.ocr_model}</dd>
            <dt>独立核验</dt>
            <dd>{runtime.data?.review_model}</dd>
            <dt>内容范围</dt>
            <dd>
              {runtime.data?.validation_scope === "content"
                ? "真实内容核验"
                : "流程测试或尚未确认"}
            </dd>
          </dl>
        )}
        <Link to="/reviews">查看人工待办与处理进度</Link>
      </section>
      <section className="study-overview-card">
        <h2>桌面偏好</h2>
        {preferences.isError && <p role="alert">{preferences.error.message}</p>}
        <label className="study-checkbox">
          <input
            type="checkbox"
            checked={preferences.data?.compact ?? false}
            disabled={!preferences.data || save.isPending}
            onChange={(event) => save.mutate({ compact: event.target.checked })}
          />
          紧凑显示研究桌面
        </label>
        <label className="study-checkbox">
          <input
            type="checkbox"
            checked={preferences.data?.restore_last_canvas ?? true}
            disabled={!preferences.data || save.isPending}
            onChange={(event) =>
              save.mutate({ restore_last_canvas: event.target.checked })
            }
          />
          进入专题时恢复上次画布
        </label>
        {save.isPending && <p role="status">正在保存偏好…</p>}
        {save.isError && (
          <p role="alert">
            {save.error.message}
            <Button
              onClick={async () => {
                await preferences.refetch();
                save.reset();
              }}
              disabled={preferences.isFetching}
            >
              重新读取偏好
            </Button>
          </p>
        )}
        {save.isSuccess && <p role="status">偏好已保存。</p>}
      </section>
      <p>
        原始环境配置和模型凭据保留在本机配置中。画布备份与恢复入口位于每张画布的管理菜单。
      </p>
    </section>
  );
}
