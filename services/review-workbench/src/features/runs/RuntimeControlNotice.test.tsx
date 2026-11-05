// @vitest-environment happy-dom
import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import RuntimeControlNotice from "./RuntimeControlNotice";
import type { components } from "@/client/schema.d.ts";

afterEach(cleanup);

function control(
  state: components["schemas"]["RuntimeControlScope"]["state"],
  code = "capacity",
): components["schemas"]["RuntimeControlReport"] {
  return {
    state,
    scopes: [
      {
        scope: "model",
        model: "gpt-6.1-sol",
        state,
        code,
        limit: 3,
        configured_limit: 7,
        retry_at: 1794000000,
        reason: "capacity",
        failures: 1,
      },
    ],
  };
}

test("cooldown identifies the affected model, limit and automatic recovery", () => {
  render(<RuntimeControlNotice control={control("cooldown")} />);
  const notice = screen.getByRole("status", { name: "自动恢复状态" });
  expect(notice.textContent).toContain("Sol：服务暂时容量不足");
  expect(notice.textContent).toContain("恢复后并发上限 3 个（配置上限 7）");
  expect(notice.textContent).toContain("后自动尝试恢复");
  expect(notice.textContent).toContain("已正常运行的任务继续完成");
  expect(screen.queryByRole("button")).toBeNull();
});

test.each(["probing", "recovering"] as const)(
  "%s explains gradual recovery",
  (state) => {
    render(<RuntimeControlNotice control={control(state)} />);
    const notice = screen.getByRole("status");
    expect(notice.textContent).toContain(
      state === "probing" ? "先用一个待处理任务" : "逐档提速",
    );
    expect(notice.textContent).toContain(
      state === "probing" ? "恢复目标并发上限 3 个" : "当前并发上限 3 个",
    );
  },
);

test.each(["authentication", "quota_exhausted"])(
  "%s needs explicit technical recovery",
  (code) => {
    render(<RuntimeControlNotice control={control("blocked", code)} />);
    const notice = screen.getByRole("status");
    expect(notice.textContent).toContain(
      code === "authentication" ? "重新登录" : "额度恢复",
    );
    expect(notice.textContent).toContain("断点恢复入口");
    expect(notice.textContent).not.toContain("暂无需操作");
    expect(screen.queryByRole("button")).toBeNull();
  },
);

test("healthy execution adds no recovery instructions", () => {
  render(<RuntimeControlNotice control={{ state: "normal", scopes: [] }} />);
  expect(screen.queryByRole("status")).toBeNull();
});
