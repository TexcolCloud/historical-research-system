import type { Draft } from "./CardFields";

type Coverage = NonNullable<Draft["coverage"]>[number];
type Range = { article_id: string; start: number; end: number; quote: string };
type Covered = {
  id: string;
  evidence_ids: string[];
  locations?: Range[];
  name?: string;
  predicate?: string;
};

const overlaps = (unit: Coverage, range: Range) =>
  unit.article_id === range.article_id &&
  unit.start < range.end &&
  range.start < unit.end;
const validRange = (range: Range) =>
  Number.isInteger(range.start) &&
  Number.isInteger(range.end) &&
  range.start >= 0 &&
  range.end > range.start &&
  Array.from(range.quote).length === range.end - range.start;

function cited(draft: Draft, object: Covered) {
  return draft.evidence.filter((span) => object.evidence_ids.includes(span.id));
}
function basis(draft: Draft, object: Covered) {
  return JSON.stringify({
    locations: object.locations ?? [],
    evidence: cited(draft, object),
  });
}
function ranges(draft: Draft, object: Covered, mention: boolean): Range[] {
  const spans = cited(draft, object).filter(
    (span) => span.article_id === draft.article_id && validRange(span),
  );
  if (!mention) return spans;
  const locations = object.locations ?? [];
  return locations.length &&
    locations.every(
      (location) =>
        location.article_id === draft.article_id &&
        validRange(location) &&
        spans.some(
          (span) =>
            span.start <= location.start &&
            location.end <= span.end &&
            Array.from(span.quote)
              .slice(location.start - span.start, location.end - span.start)
              .join("") === location.quote,
        ),
    )
    ? locations
    : [];
}

/** Update only mechanical source membership; frozen coverage text and order stay intact. */
export function synchronizeCardCoverage(draft: Draft, initial: Draft) {
  let coverage = (draft.coverage ?? []).map((unit) => ({ ...unit }));
  const missing: string[] = [];
  for (const [collection, field, label] of [
    ["mentions", "mention_ids", "实体"],
    ["events", "event_ids", "事件"],
    ["relations", "relation_ids", "关系"],
  ] as const) {
    const objects = draft[collection] ?? [];
    const before = new Map(
      (initial[collection] ?? []).map((item) => [item.id, item]),
    );
    const retained = new Set(
      objects
        .filter((object) => {
          const old = before.get(object.id);
          return (
            old &&
            basis(initial, old) === basis(draft, object) &&
            coverage.some((unit) => unit[field]?.includes(object.id))
          );
        })
        .map((object) => object.id),
    );
    coverage = coverage.map((unit) => ({
      ...unit,
      [field]: (unit[field] ?? []).filter((id) => retained.has(id)),
    }));
    for (const object of objects) {
      if (retained.has(object.id)) continue;
      const located = ranges(draft, object, collection === "mentions");
      const matching = coverage.filter((unit) =>
        located.some((range) => overlaps(unit, range)),
      );
      if (
        !located.length ||
        !matching.length ||
        located.some((range) => !matching.some((unit) => overlaps(unit, range)))
      ) {
        missing.push(
          `${label}：${"name" in object ? object.name : object.predicate}`,
        );
        continue;
      }
      for (const unit of matching)
        unit[field] = [...new Set([...(unit[field] ?? []), object.id])];
    }
  }
  return { card: { ...draft, coverage }, missing };
}
