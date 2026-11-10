import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { research } from "@/features/research/api";
import { readingNavigationOptions } from "./navigation-query";
import { chapterOptions, chaptersOptions } from "./chapter-query";

/** Own chapter selection, source-version query keys and bounded neighbor prefetch. */
export function useReaderData(bookId: string, params: URLSearchParams) {
  const query = useQueryClient();
  const navigation = useQuery(readingNavigationOptions(bookId));
  const chapters = useQuery(chaptersOptions(bookId));
  const requested = params.get("chapter");
  const requestedPage = Number(params.get("physicalPage"));
  const id =
    chapters.data?.find((item) => item.id === requested)?.id ||
    chapters.data?.find(
      (item) => requestedPage > 0 && item.pages.includes(requestedPage),
    )?.id ||
    chapters.data?.[0]?.id ||
    "";
  const chapter = useQuery({ ...chapterOptions(id), enabled: Boolean(id) });
  const researchReading = useQuery({
    queryKey: [
      "research",
      "reading",
      id,
      chapter.data?.run_id,
      chapter.data?.text_sha256,
    ],
    queryFn: ({ signal }) => research.reading(id, signal),
    enabled: Boolean(id && chapter.data?.text_sha256),
    staleTime: 30000,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const index = chapters.data?.findIndex((item) => item.id === id) ?? -1;
  const next = chapters.data?.[index + 1],
    previous = chapters.data?.[index - 1];
  useEffect(() => {
    for (const entry of [next, previous])
      if (entry) void query.prefetchQuery(chapterOptions(entry.id));
  }, [next, previous, query]);
  const prefetchChapter = (chapterId: string) =>
    query.prefetchQuery(chapterOptions(chapterId));
  return {
    navigation,
    chapters,
    chapter,
    researchReading,
    id,
    next,
    previous,
    prefetchChapter,
  };
}
export type ReaderData = ReturnType<typeof useReaderData>;
