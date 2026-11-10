import { describe, expect, it } from "vitest";
import {
  readingHref,
  readingRange,
  requestedReadingTarget,
  safeReaderReturn,
} from "./reader-location";

const target = {
  chapter_id: "chapter-b",
  run_id: "run",
  physical_page: 12,
  start: 5,
  end: 8,
  text_sha256: "a".repeat(64),
  anchor: "标题乙",
  image_sha256: "b".repeat(64),
};
describe("shared source-bound reading targets", () => {
  it("restores cross-book research results while rejecting external return routes", () => {
    expect(
      safeReaderReturn("/research?q=运输&book_id=book-b&card=card-a", "book-a"),
    ).toBe("/research?q=运输&book_id=book-b&card=card-a");
    expect(
      safeReaderReturn("/books/book-a?tab=research&card=card-a", "book-a"),
    ).toBe("/books/book-a?tab=research&card=card-a");
    expect(safeReaderReturn("//example.com/research?q=运输", "book-a")).toBe(
      "/books/book-a?tab=reader&section=directory",
    );
  });
  it("opens text even when the caller is in the directory or search section", () => {
    const href = readingHref("book", target, {
      returnTo: "/books/book?tab=reader&section=directory",
    });
    const url = new URL(href, "http://localhost");
    expect(url.searchParams.get("section")).toBe("text");
    expect(url.searchParams.get("chapter")).toBe("chapter-b");
    expect(requestedReadingTarget(url.searchParams)).toEqual({
      ...target,
      anchor: "",
      image_sha256: "",
    });
    expect(url.searchParams.get("returnTo")).toContain("section=directory");
  });
  it("projects canonical codepoint offsets through unchanged reading segments", () => {
    expect(
      readingRange(target, {
        id: target.chapter_id,
        run_id: target.run_id,
        text_sha256: target.text_sha256,
        text: "😀前文段落标题乙后文",
        reading_text: "前文段落标题乙后文",
        reading_segments: [
          { start: 1, end: 10, reading_start: 0, reading_end: 9, exact: true },
        ],
      }),
    ).toEqual({ start: 4, end: 7 });
  });
  it("does not guess positions in changed tables or another publication", () => {
    const chapter = {
      id: target.chapter_id,
      run_id: target.run_id,
      text_sha256: target.text_sha256,
      text: "😀前文段落标题乙后文",
      reading_text: "合并后的表格",
      reading_segments: [
        { start: 0, end: 10, reading_start: 0, reading_end: 6, exact: false },
      ],
    };
    expect(readingRange(target, chapter)).toBeNull();
    expect(
      readingRange({ ...target, text_sha256: "c".repeat(64) }, chapter),
    ).toBeNull();
    expect(readingRange({ ...target, run_id: "old" }, chapter)).toBeNull();
  });
  it("maps an unchanged title immediately after a replaced block", () => {
    expect(
      readingRange(
        { ...target, start: 3, end: 8, anchor: "TITLE" },
        {
          id: target.chapter_id,
          run_id: target.run_id,
          text_sha256: target.text_sha256,
          text: "abcTITLE",
          reading_text: "changedTITLE",
          reading_segments: [
            {
              start: 0,
              end: 3,
              reading_start: 0,
              reading_end: 7,
              exact: false,
            },
            {
              start: 3,
              end: 8,
              reading_start: 7,
              reading_end: 12,
              exact: true,
            },
          ],
        },
      ),
    ).toEqual({ start: 7, end: 12 });
  });
});
