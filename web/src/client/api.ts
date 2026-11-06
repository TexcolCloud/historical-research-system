import createClient from "openapi-fetch";
import type { paths, components } from "@/client/schema.d.ts";

export const client = createClient<paths>();
client.use({
  onError({ request, error }) {
    if (error instanceof Error && error.name === "AbortError") return error;
    return new Error(
      request.method === "GET"
        ? "无法连接本机服务。请确认工作台服务已启动，再重新读取。"
        : "连接中断，尚未确认本次操作结果。请保持当前页面，连接恢复后重试。",
      { cause: error },
    );
  },
});
export type Book = components["schemas"]["BookSummary"];
export type UploadSession = components["schemas"]["UploadSession"];
export type UploadRequest = components["schemas"]["UploadRequest"];

export type UploadIdentity = { requestId: string; token: string };
export async function requestUpload(
  body: UploadRequest,
  identity: UploadIdentity,
): Promise<UploadSession> {
  const { data, response } = await client.POST("/api/v2/uploads", {
    body,
    params: {
      header: {
        "idempotency-key": identity.requestId,
        "x-upload-token": identity.token,
      },
    },
  });
  if (!data)
    throw new Error(
      response.status === 422
        ? "请选择大小不超过 256 MB 的 PDF 文件。"
        : "暂时无法创建上传，请重试。",
    );
  return data;
}

export async function readBooks(signal?: AbortSignal): Promise<Book[]> {
  const books = new Map<string, Book>();
  for (let offset = 0; ; offset += 100) {
    const { data } = await client.GET("/api/v2/books", {
      signal,
      params: { query: { offset, limit: 100 } },
    });
    if (!data) throw new Error("暂时无法读取书籍，请稍后重试。");
    data.forEach((book) => books.set(book.id, book));
    if (data.length < 100) return [...books.values()];
  }
}

export async function readReviewIssues(runId: string, signal?: AbortSignal) {
  const issues = new Map<string, components["schemas"]["ReviewIssueSummary"]>();
  for (let offset = 0; ; offset += 100) {
    const { data } = await client.GET("/api/v2/reviews", {
      signal,
      params: { query: { run_id: runId, pending: true, offset, limit: 100 } },
    });
    if (!data) throw new Error("无法读取核对清单，请重试。");
    data.forEach((issue) => issues.set(issue.id, issue));
    if (data.length < 100) return [...issues.values()];
  }
}
