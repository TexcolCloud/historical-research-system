import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "vitest";
import type { components } from "@/client/schema";
import { synchronizeCardCoverage } from "./card-coverage";

function fixture(): components["schemas"]["CardOnlyCardDraft"] {
  return JSON.parse(
    readFileSync(
      resolve(
        process.cwd(),
        "src/features/research/card-revision-contract.fixture.json",
      ),
      "utf8",
    ),
  ).initial;
}

test("removed objects leave no dangling coverage IDs and retain all original coverage text and evidence", () => {
  const initial = fixture();
  const changed = { ...initial, mentions: [], events: [], relations: [] };
  const result = synchronizeCardCoverage(changed, initial);
  expect(result.missing).toEqual([]);
  expect(result.card).toEqual({
    ...changed,
    coverage: initial.coverage?.map((unit) => ({
      ...unit,
      mention_ids: [],
      event_ids: [],
      relation_ids: [],
    })),
  });
  expect(initial.coverage?.[1].mention_ids).toEqual(["li"]);
});

test("moving locations or changing the range of the same evidence ID moves all affected object memberships", () => {
  const initial = fixture();
  const changed = structuredClone(initial);
  changed.evidence[1] = {
    ...changed.evidence[1],
    start: 0,
    end: 9,
    quote: changed.evidence[0].quote,
  };
  changed.mentions![0].locations = [
    { article_id: "primary", start: 3, end: 6, quote: "某文件" },
  ];
  const result = synchronizeCardCoverage(changed, initial);
  expect(result.missing).toEqual([]);
  expect(result.card.coverage).toEqual(
    initial.coverage?.map((unit, index) => ({
      ...unit,
      mention_ids: index ? [] : ["li"],
      event_ids: index ? [] : ["record"],
      relation_ids: index ? [] : ["participates"],
    })),
  );
});

test("external aliases with colliding offsets cannot supply new primary-article coverage", () => {
  const initial = fixture();
  const changed = structuredClone(initial);
  changed.evidence.push({
    ...changed.evidence[0],
    id: "external",
    article_id: "foreign-notes-alias",
  });
  changed.mentions!.push({
    ...changed.mentions![0],
    id: "new-mention",
    name: "新文献",
    evidence_ids: ["external"],
    locations: [{ article_id: "primary", start: 3, end: 6, quote: "某文件" }],
  });
  changed.events!.push({
    ...changed.events![0],
    id: "new-event",
    name: "新事件",
    evidence_ids: ["external"],
  });
  changed.relations!.push({
    ...changed.relations![0],
    id: "new-relation",
    predicate: "新关系",
    evidence_ids: ["external"],
  });
  const result = synchronizeCardCoverage(changed, initial);
  expect(result.missing).toEqual([
    "实体：新文献",
    "事件：新事件",
    "关系：新关系",
  ]);
  expect(result.card.coverage).toEqual(initial.coverage);
  expect(result.card.mentions).toEqual(changed.mentions);
  expect(result.card.evidence).toEqual(changed.evidence);
});

test("half-open boundaries, repeated occurrences and spans crossing parts have exact stable memberships", () => {
  const initial = fixture();
  const changed = structuredClone(initial);
  changed.evidence.push({
    id: "crossing",
    article_id: "primary",
    start: 8,
    end: 10,
    quote: "\n李",
    footnote_ids: [],
  });
  changed.mentions!.push({
    ...changed.mentions![0],
    id: "multiple",
    evidence_ids: ["first", "second"],
    locations: [
      { article_id: "primary", start: 0, end: 1, quote: "前" },
      { article_id: "primary", start: 9, end: 11, quote: "李四" },
      { article_id: "primary", start: 9, end: 11, quote: "李四" },
    ],
  });
  changed.events!.push({ ...changed.events![0], id: "at-boundary" });
  changed.relations!.push({
    ...changed.relations![0],
    id: "crossing-relation",
    evidence_ids: ["crossing", "crossing"],
  });
  const result = synchronizeCardCoverage(changed, initial);
  expect(result.missing).toEqual([]);
  expect(
    result.card.coverage.map((unit) => [
      unit.mention_ids,
      unit.event_ids,
      unit.relation_ids,
    ]),
  ).toEqual([
    [["multiple"], [], ["crossing-relation"]],
    [
      ["li", "multiple"],
      ["record", "at-boundary"],
      ["participates", "crossing-relation"],
    ],
  ]);
});

test.each(["removed-reference", "changed-source-quote"])(
  "edited occurrences lose their old membership when their precise evidence is invalid: %s",
  (reason) => {
    const initial = fixture();
    const changed = structuredClone(initial);
    if (reason === "removed-reference")
      changed.mentions![0].evidence_ids = ["first"];
    else changed.evidence[1].quote = "别名另记。";
    const result = synchronizeCardCoverage(changed, initial);
    expect(result.missing).toContain("实体：李四");
    expect(
      result.card.coverage.every((unit) => !unit.mention_ids?.includes("li")),
    ).toBe(true);
    expect(result.card.mentions).toEqual(changed.mentions);
  },
);

test("unchanged objects retain their previously checked coverage rather than being reordered by other edits", () => {
  const initial = fixture();
  initial.evidence.push({
    ...initial.evidence[0],
    id: "frozen-notes",
    article_id: "old-notes-alias",
  });
  initial.events![0].evidence_ids = ["frozen-notes"];
  initial.relations![0].evidence_ids = ["frozen-notes"];
  const changed = { ...initial, summary: "只修订提要，原有冻结出处不变。" };
  const result = synchronizeCardCoverage(changed, initial);
  expect(result.missing).toEqual([]);
  expect(result.card).toEqual(changed);
});
