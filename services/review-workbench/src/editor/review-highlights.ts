import { marked } from "marked";
import { z } from "zod";

export type ReviewLocation = {
  id: string;
  page: number;
  anchor: { exact: string; prefix: string; suffix: string };
  focus: string;
  boxes: number[][];
  image_precision: string;
  message: string;
  source_reading: string;
  candidate: string;
  suggestion: string;
  location?: string;
  insertion_offset?: number;
};
const coordinate = z.number().finite().min(0).max(1);
const locationSchema = z.object({
  id: z.string(),
  page: z.number(),
  anchor: z.object({
    exact: z.string(),
    prefix: z.string(),
    suffix: z.string(),
  }),
  focus: z.string(),
  boxes: z.array(
    z
      .tuple([coordinate, coordinate, coordinate, coordinate])
      .refine((b) => b[0] < b[2] && b[1] < b[3]),
  ),
  image_precision: z.string(),
  message: z.string(),
  source_reading: z.string(),
  candidate: z.string(),
  suggestion: z.string(),
  location: z.string().optional(),
  insertion_offset: z.number().int().nonnegative().optional(),
});
export function readLocations(rows: unknown[]): ReviewLocation[] {
  return rows.flatMap((row) => {
    const parsed = locationSchema.safeParse(row);
    return parsed.success ? [parsed.data] : [];
  });
}

export function resolveLocation(text: string, issue: ReviewLocation) {
  const { exact, prefix, suffix } = issue.anchor;
  const starts: number[] = [];
  for (let at = 0; exact && (at = text.indexOf(exact, at)) >= 0; at++) {
    if (
      (!prefix || text.slice(0, at).endsWith(prefix)) &&
      (!suffix || text.slice(at + exact.length).startsWith(suffix))
    )
      starts.push(at);
  }
  const offset = exact.indexOf(issue.focus);
  if (
    starts.length === 1 &&
    issue.insertion_offset !== undefined &&
    issue.insertion_offset <= Array.from(exact).length
  ) {
    const at =
      starts[0] +
      Array.from(exact).slice(0, issue.insertion_offset).join("").length;
    return { start: at, end: at };
  }
  if (
    starts.length !== 1 ||
    !issue.focus ||
    offset < 0 ||
    exact.indexOf(issue.focus, offset + 1) >= 0
  )
    return null;
  return {
    start: starts[0] + offset,
    end: starts[0] + offset + issue.focus.length,
  };
}

/** A full-anchor suggestion must retain the context outside the highlighted span. */
export function suggestionEdit(text: string, issue: ReviewLocation) {
  const range = resolveLocation(text, issue);
  if (!range || !issue.suggestion) return null;
  const exact = issue.anchor.exact;
  const relative =
    issue.insertion_offset !== undefined
      ? Array.from(exact).slice(0, issue.insertion_offset).join("").length
      : exact.indexOf(issue.focus);
  const before = exact.slice(0, relative);
  const after = exact.slice(
    relative + (issue.insertion_offset !== undefined ? 0 : issue.focus.length),
  );
  const proposed = issue.suggestion;
  if (
    proposed.length >= before.length + after.length &&
    proposed.startsWith(before) &&
    proposed.endsWith(after)
  )
    return {
      ...range,
      replacement: proposed.slice(
        before.length,
        proposed.length - after.length,
      ),
    };
  // Single-character proposals also have an unambiguous focus-only scope.
  if (Array.from(proposed).length === 1)
    return { ...range, replacement: proposed };
  return null;
}

/** Locate a resolved edit by its unchanged surrounding context, including restored drafts. */
export function decisionRange(text: string, issue: ReviewLocation) {
  const exact = resolveLocation(text, issue);
  if (exact) return exact;
  const at =
    issue.insertion_offset === undefined
      ? issue.anchor.exact.indexOf(issue.focus)
      : Array.from(issue.anchor.exact).slice(0, issue.insertion_offset).join("")
          .length;
  if (at < 0) return null;
  const before = issue.anchor.prefix + issue.anchor.exact.slice(0, at);
  const after =
    issue.anchor.exact.slice(
      at + (issue.insertion_offset === undefined ? issue.focus.length : 0),
    ) + issue.anchor.suffix;
  if (!before || !after) return null;
  const left: number[] = [],
    right: number[] = [];
  if (!before) left.push(0);
  else
    for (let p = 0; (p = text.indexOf(before, p)) >= 0; p++)
      left.push(p + before.length);
  if (!after) right.push(text.length);
  else for (let p = 0; (p = text.indexOf(after, p)) >= 0; p++) right.push(p);
  if (left.length !== 1 || right.length !== 1 || left[0] > right[0])
    return null;
  return { start: left[0], end: right[0] };
}

/** Carry known positions through a single editor change; broad overlapping changes stay unlocated. */
export function moveReviewLocations(
  before: string,
  after: string,
  locations: ReviewLocation[],
) {
  if (before === after) return locations;
  let start = 0,
    end = before.length,
    nextEnd = after.length;
  while (start < end && start < nextEnd && before[start] === after[start])
    start++;
  while (
    end > start &&
    nextEnd > start &&
    before[end - 1] === after[nextEnd - 1]
  ) {
    end--;
    nextEnd--;
  }
  const delta = after.length - before.length;
  return locations.map((location) => {
    const range = decisionRange(before, location);
    if (!range) return location;
    let left = range.start,
      right = range.end;
    if (end <= left && start < left) {
      left += delta;
      right += delta;
    } else if (start >= right && !(left === right && start === left)) {
      /* position unchanged */
    } else if (start >= left && end <= right) {
      right += delta;
    } else return location;
    const a = Math.max(0, left - 32),
      b = Math.min(after.length, right + 32);
    const { insertion_offset: _old, ...rest } = location;
    return {
      ...rest,
      focus: after.slice(left, right),
      anchor:
        left === right
          ? {
              exact: after.slice(a, b),
              prefix: after.slice(Math.max(0, a - 24), a),
              suffix: after.slice(b, b + 24),
            }
          : {
              exact: after.slice(left, right),
              prefix: after.slice(a, left),
              suffix: after.slice(right, b),
            },
      ...(left === right
        ? { insertion_offset: Array.from(after.slice(a, left)).length }
        : {}),
    };
  });
}

function visible(raw: string) {
  const parsed = new DOMParser().parseFromString(
    marked.parse(raw, { async: false }),
    "text/html",
  );
  return (parsed.body.textContent ?? "").trimEnd();
}
export function renderedRange(joined: string, issue: ReviewLocation) {
  const definition = issue.anchor.exact.match(/^\[\^[^\]]+\]:\s*/);
  const anchor =
    definition &&
    issue.anchor.exact.indexOf(issue.focus) >= definition[0].length
      ? issue.anchor.exact.slice(definition[0].length)
      : issue.anchor.exact;
  const exact = visible(anchor),
    focus = visible(issue.focus);
  const prefix = visible(issue.anchor.prefix),
    suffix = visible(issue.anchor.suffix),
    matches: number[] = [];
  for (let at = 0; exact && (at = joined.indexOf(exact, at)) >= 0; at++) {
    if (
      (!prefix || joined.slice(0, at).endsWith(prefix)) &&
      (!suffix || joined.slice(at + exact.length).startsWith(suffix))
    )
      matches.push(at);
  }
  const relative = exact.indexOf(focus);
  if (matches.length === 1 && issue.insertion_offset !== undefined) {
    const before = Array.from(issue.anchor.exact)
      .slice(0, issue.insertion_offset)
      .join("");
    // An incomplete Markdown token has no reliable visible offset: keep source-mode positioning.
    if (/[*_`<>\[\]]/.test(before)) return null;
    const at = matches[0] + visible(before).length;
    return { start: at, end: at };
  }
  return matches.length === 1 &&
    focus &&
    relative >= 0 &&
    exact.indexOf(focus, relative + 1) < 0
    ? {
        start: matches[0] + relative,
        end: matches[0] + relative + focus.length,
      }
    : null;
}

/** Decorate sanitized DOM text nodes; never splice markup into source Markdown. */
export function highlightDom(
  root: HTMLElement,
  text: string,
  issues: ReviewLocation[],
  active?: string,
) {
  for (const old of root.querySelectorAll<HTMLElement>("mark[data-review-id]"))
    old.dataset.reviewInsertion !== undefined
      ? old.remove()
      : old.replaceWith(...old.childNodes);
  root.normalize();
  for (const issue of issues) {
    if (!resolveLocation(text, issue)) continue;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes: Text[] = [];
    let node: Node | null;
    while ((node = walker.nextNode()))
      if (!node.parentElement?.closest("mark[data-review-insertion]"))
        nodes.push(node as Text);
    const joined = nodes.map((n) => n.data).join("");
    const projected = renderedRange(joined, issue);
    if (!projected) continue;
    const { start, end } = projected;
    let offset = 0;
    for (const leaf of nodes) {
      const next = offset + leaf.length;
      if (start === end && start >= offset && start <= next) {
        const range = document.createRange();
        range.setStart(leaf, start - offset);
        range.collapse(true);
        const mark = document.createElement("mark");
        mark.dataset.reviewId = issue.id;
        mark.dataset.reviewInsertion = "";
        mark.textContent = "▏";
        mark.className =
          issue.id === active
            ? "review-mark review-mark-active"
            : "review-mark";
        mark.tabIndex = 0;
        mark.setAttribute("aria-label", "缺字插入位置");
        range.insertNode(mark);
        break;
      }
      const from = Math.max(start, offset) - offset,
        to = Math.min(end, next) - offset;
      if (to > from) {
        const range = document.createRange();
        range.setStart(leaf, from);
        range.setEnd(leaf, to);
        const mark = document.createElement("mark");
        mark.dataset.reviewId = issue.id;
        mark.className =
          issue.id === active
            ? "review-mark review-mark-active"
            : "review-mark";
        mark.tabIndex = 0;
        mark.setAttribute("aria-label", "待核疑点");
        range.surroundContents(mark);
      }
      offset = next;
    }
  }
  if (active)
    root
      .querySelector<HTMLElement>(
        `mark[data-review-id="${CSS.escape(active)}"]`,
      )
      ?.scrollIntoView?.({ block: "center" });
}
