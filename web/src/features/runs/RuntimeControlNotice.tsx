import type { components } from "@/client/schema.d.ts";

type Control = components["schemas"]["RuntimeControlReport"];
type Scope = components["schemas"]["RuntimeControlScope"];

export const runtimeControlTitles = {
  cooldown: "等待服务恢复",
  probing: "正在尝试恢复处理",
  recovering: "正在逐步恢复处理速度",
  blocked: "自动处理等待恢复",
};

function scopeName(scope: Scope) {
  if (scope.scope === "account") return "当前账户";
  if (scope.model === "gpt-6.1-terra") return "Terra";
  if (scope.model === "gpt-6.1-sol") return "Sol";
  return scope.model || "当前模型";
}

function cause(scope: Scope) {
  if (scope.reason === "retry_budget_exhausted") return "自动尝试次数已达上限";
  if (scope.reason === "wait_budget_exhausted") return "自动恢复等待已达上限";
  switch (scope.code) {
    case "capacity":
    case "service_unavailable":
      return "服务暂时容量不足";
    case "rate_limit":
    case "429":
      return "请求受到限流";
    case "quota_exhausted":
      return "账户额度已用尽，请在额度恢复后从断点恢复";
    case "authentication":
      return "登录已失效，请重新登录后从断点恢复";
    case "transport":
      return "连接暂时中断";
    case "timeout":
      return "处理超时";
    default:
      return "处理遇到技术故障";
  }
}

export default function RuntimeControlNotice({
  control,
}: {
  control?: Control | null;
}) {
  if (!control || control.state === "normal") return null;
  const scopes = (control.scopes ?? []).filter(
    (scope) => scope.state !== "normal",
  );
  const title = runtimeControlTitles[control.state];
  return (
    <div
      className="workspace-next-action runtime-control-note"
      role="status"
      aria-label="自动恢复状态"
      data-state={control.state}
    >
      <strong>{title}</strong>
      {scopes.map((scope) => (
        <p key={`${scope.scope}:${scope.model ?? "account"}`}>
          {scopeName(scope)}：{cause(scope)}。
          {scope.state === "probing"
            ? "先用一个待处理任务验证服务，成功后再逐档提速。"
            : scope.state === "recovering"
              ? "正在根据实际处理结果逐档提速。"
              : scope.state === "cooldown" && scope.retry_at
                ? `预计 ${new Date(scope.retry_at * 1000).toLocaleTimeString()} 后自动尝试恢复。`
                : ""}
        </p>
      ))}
      <p>
        已完成结果已保存。
        {control.state === "blocked"
          ? "请处理上述原因，再使用断点恢复入口继续。"
          : "系统会自动恢复，暂无需操作；已正常运行的任务继续完成。"}
      </p>
      <details>
        <summary>查看恢复与并发详情</summary>
        {scopes.map((scope) => (
          <p key={`${scope.scope}:${scope.model ?? "account"}`}>
            {scopeName(scope)}：
            {scope.state === "cooldown"
              ? `恢复后并发上限 ${scope.limit} 个（配置上限 ${scope.configured_limit}）。`
              : scope.state === "blocked"
                ? "已暂停该范围的新任务。"
                : scope.state === "probing"
                  ? `恢复目标并发上限 ${scope.limit} 个（配置上限 ${scope.configured_limit}）。`
                  : `当前并发上限 ${scope.limit} 个（配置上限 ${scope.configured_limit}）。`}
          </p>
        ))}
      </details>
    </div>
  );
}
