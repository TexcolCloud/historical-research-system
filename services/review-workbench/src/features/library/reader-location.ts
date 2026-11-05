import type { components } from "@/client/schema";

export type ReadingTarget = components["schemas"]["ReadingTarget"];
type Chapter = components["schemas"]["Chapter"];
type ReadingChapter = Pick<Chapter, "id" | "run_id" | "text"> &
  Partial<Pick<Chapter, "text_sha256" | "reading_text" | "reading_segments">>;

/** Directory, inline TOC and search links all enter the same source-bound reader route. */
export function readingHref(
  bookId: string,
  target: ReadingTarget,
  options?: { returnTo?: string },
) {
  const params = new URLSearchParams({
    tab: "reader",
    section: "text",
    chapter: target.chapter_id,
    physicalPage: String(target.physical_page),
    start: String(target.start),
    end: String(target.end),
    textSha: target.text_sha256,
    run: target.run_id,
  });
  if (options?.returnTo) params.set("returnTo", options.returnTo);
  return `/books/${bookId}?${params}`;
}

export function requestedReadingTarget(
  params: URLSearchParams,
): ReadingTarget | null {
  const chapter_id = params.get("chapter"),
    run_id = params.get("run"),
    text_sha256 = params.get("textSha");
  const rawStart = params.get("start"),
    rawEnd = params.get("end"),
    rawPage = params.get("physicalPage");
  if (
    !chapter_id ||
    !run_id ||
    !text_sha256 ||
    !/^[a-f0-9]{64}$/.test(text_sha256) ||
    rawStart == null ||
    rawEnd == null ||
    rawPage == null
  )
    return null;
  const start = Number(rawStart),
    end = Number(rawEnd),
    physical_page = Number(rawPage);
  if (
    ![start, end, physical_page].every(Number.isSafeInteger) ||
    start < 0 ||
    end < start ||
    physical_page < 1
  )
    return null;
  return {
    chapter_id,
    run_id,
    text_sha256,
    start,
    end,
    physical_page,
    anchor: "",
    image_sha256: "",
  };
}

/** Map only retained exact source spans; replacement displays explicitly fall back to originals. */
export function readingRange(target: ReadingTarget, chapter: ReadingChapter) {
  if (
    target.chapter_id !== chapter.id ||
    target.run_id !== chapter.run_id ||
    !chapter.text_sha256 ||
    target.text_sha256 !== chapter.text_sha256 ||
    target.start < 0 ||
    target.end < target.start ||
    target.end > Array.from(chapter.text).length
  )
    return null;
  if (
    target.anchor &&
    Array.from(chapter.text).slice(target.start, target.end).join("") !==
      target.anchor
  )
    return null;
  if (chapter.reading_text == null || chapter.reading_text === chapter.text)
    return { start: target.start, end: target.end };
  const segments = chapter.reading_segments ?? [];
  let cursor = target.start,
    mappedStart: number | null = null,
    mappedEnd: number | null = null;
  for (const segment of segments) {
    if (
      segment.end < cursor ||
      (target.start < target.end && segment.end === cursor) ||
      segment.start > target.end
    )
      continue;
    const from = Math.max(cursor, segment.start),
      to = Math.min(target.end, segment.end);
    if (
      from > cursor ||
      to < from ||
      !segment.exact ||
      segment.end - segment.start !==
        segment.reading_end - segment.reading_start
    )
      return null;
    const left = segment.reading_start + from - segment.start;
    if (mappedEnd != null && left !== mappedEnd) return null;
    mappedStart ??= left;
    mappedEnd = segment.reading_start + to - segment.start;
    cursor = to;
    if (cursor === target.end) return { start: mappedStart, end: mappedEnd };
  }
  return null;
}

export function safeReaderReturn(value: string | null, bookId: string) {
  return value?.startsWith(`/books/${bookId}?`)
    ? value
    : `/books/${bookId}?tab=reader&section=directory`;
}
