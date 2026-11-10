import { expect, test } from "vitest";
import { researchReadingLinks } from "./reading";
test("research annotations link only the exact retained source span and reject stale same-name matches", () => {
  const sha = "a".repeat(64);
  const chapter = {
    id: "chapter",
    run_id: "run",
    text: "甲见张某。乙见张某。",
    text_sha256: sha,
  };
  const data = {
    chapter_id: "chapter",
    run_id: "run",
    text_sha256: sha,
    source_current: true,
    cards: [],
    annotations: [
      {
        id: "one",
        entity_id: "person-one",
        kind: "person",
        name: "张某",
        start: 2,
        end: 4,
        quote: "张某",
        text_sha256: sha,
        card_id: "c",
      },
      {
        id: "stale",
        entity_id: "person-two",
        kind: "person",
        name: "张某",
        start: 7,
        end: 9,
        quote: "张某",
        text_sha256: "b".repeat(64),
        card_id: "c",
      },
    ],
  };
  const links = researchReadingLinks(
    chapter,
    data,
    "/books/book?tab=reader&chapter=chapter",
  );
  expect(links).toHaveLength(1);
  expect(links[0]).toMatchObject({ start: 2, end: 4, label: "查看人物：张某" });
  expect(links[0].href).toContain("entity=person-one");
  expect(
    researchReadingLinks(
      chapter,
      { ...data, text_sha256: "c".repeat(64) },
      "/books/book?tab=reader",
    ),
  ).toEqual([]);
});
