// @vitest-environment happy-dom
import { expect, test } from "vitest";
import { client } from "./api.ts";

test("connection errors explain uncertain writes and preserve cancellation", async () => {
  const failure = new TypeError("Failed to fetch");
  await expect(
    client.POST("/api/v2/runs/{run_id}/retry", {
      params: { path: { run_id: "run" } },
      body: { request_id: "one" },
      fetch: async () => {
        throw failure;
      },
    }),
  ).rejects.toMatchObject({
    message: "连接中断，尚未确认本次操作结果。请保持当前页面，连接恢复后重试。",
    cause: failure,
  });
  const cancelled = new DOMException("Aborted", "AbortError");
  await expect(
    client.GET("/api/v2/books", {
      fetch: async () => {
        throw cancelled;
      },
    }),
  ).rejects.toBe(cancelled);
});
