import createClient from "openapi-fetch";
import type { paths, components } from "./schema.d.ts";

export const client = createClient<paths>();
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

export async function readCards(bookId?: string, signal?: AbortSignal) {
  const cards = new Map<string, components["schemas"]["CardSummary"]>();
  for (let offset = 0; ; offset += 100) {
    const { data } = await client.GET("/api/v2/cards", {
      signal,
      params: { query: { book_id: bookId, offset, limit: 100 } },
    });
    if (!data) throw new Error("暂时无法读取史料卡，请重试。");
    data.forEach((card) => cards.set(card.id, card));
    if (data.length < 100) return [...cards.values()];
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
