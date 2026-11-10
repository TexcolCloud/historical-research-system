import { queryOptions } from "@tanstack/react-query";
import { client } from "@/client/api";

/** One transport path; callers retain their source-hash and cache identities. */
export async function readChapter(
  id: string,
  signal?: AbortSignal,
  unavailableMessage = "章节暂时无法读取，请重试。",
) {
  const { data } = await client.GET("/api/v2/chapters/{chapter_id}", {
    params: { path: { chapter_id: id } },
    signal,
  });
  if (!data) throw new Error(unavailableMessage);
  return data;
}

export const chapterOptions = (id: string) =>
  queryOptions({
    queryKey: ["platform", "chapter", id],
    staleTime: Infinity,
    queryFn: ({ signal }) => readChapter(id, signal),
  });

export const chaptersOptions = (bookId: string) =>
  queryOptions({
    queryKey: ["platform", "chapters", bookId],
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/books/{book_id}/chapters", {
        params: { path: { book_id: bookId } },
        signal,
      });
      if (!data) throw new Error("目录暂时无法读取，请重试。");
      return data;
    },
  });
