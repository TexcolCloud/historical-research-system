import {
  readingRange,
  type ReadingTarget,
} from "@/features/library/reader-location";
import type { ResearchReading } from "./api";

/** Only published source positions can become links; a name match alone is never sufficient. */
export function researchReadingLinks(
  chapter: Parameters<typeof readingRange>[1],
  data: ResearchReading | undefined,
  returnTo: string,
) {
  if (
    !data ||
    data.source_current === false ||
    data.chapter_id !== chapter.id ||
    data.run_id !== chapter.run_id ||
    data.text_sha256 !== chapter.text_sha256
  )
    return [];
  const chars = Array.from(chapter.text);
  return (data.annotations ?? []).flatMap((item) => {
    if (
      !item.entity_id ||
      !Number.isSafeInteger(item.start) ||
      !Number.isSafeInteger(item.end) ||
      item.end <= item.start ||
      item.text_sha256 !== chapter.text_sha256 ||
      chars.slice(item.start, item.end).join("") !== item.quote
    )
      return [];
    const target: ReadingTarget = {
      chapter_id: chapter.id,
      run_id: chapter.run_id,
      text_sha256: item.text_sha256,
      start: item.start,
      end: item.end,
      anchor: item.quote,
      physical_page: 1,
      image_sha256: "",
    };
    const range = readingRange(target, chapter);
    if (!range) return [];
    const kind =
      item.kind === "person" ? "人物" : item.kind === "event" ? "事件" : "实体";
    return [
      {
        ...range,
        id: `research-${item.id}`,
        label: `查看${kind}：${item.name}`,
        href: `#/research?${new URLSearchParams({ entity: item.entity_id, returnTo })}`,
      },
    ];
  });
}
