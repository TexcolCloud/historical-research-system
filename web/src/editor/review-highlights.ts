import { marked } from "marked";
import { z } from "zod";
import type { components } from "@/client/schema.d.ts";

export type ReviewTextSpan = {
  anchor: { exact: string; prefix: string; suffix: string };
  focus: string;
  start?: number | null;
  end?: number | null;
  offset_unit?: "unicode_codepoint";
  text_precision?: "character" | "phrase" | "insertion" | "page";
  insertion_offset?: number | null;
  text_status?: "located" | "missing" | "ambiguous";
  location_diagnostics?: string[];
  unlocated_reason?: string;
};
export type ReviewLocation = ReviewTextSpan & {
  id: string;
  page: number;
  boxes: number[][];
  image_precision: "line" | "region" | "page";
  message: string;
  source_reading: string;
  candidate: string;
  suggestion: string;
  location?: string;
  text_sha256?: string;
  image_sha256?: string;
  text_spans?: ReviewTextSpan[];
};
const coordinate = z.number().finite().min(0).max(1);
const boxSchema = z
  .tuple([coordinate, coordinate, coordinate, coordinate])
  .refine((b) => b[0] < b[2] && b[1] < b[3]);
const anchorSchema = z.object({
  exact: z.string().catch(""),
  prefix: z.string().catch(""),
  suffix: z.string().catch(""),
});
const spanSchema = z.object({
  anchor: anchorSchema.catch({ exact: "", prefix: "", suffix: "" }),
  focus: z.string().catch(""),
  start: z.number().int().nonnegative().nullish().catch(null),
  end: z.number().int().nonnegative().nullish().catch(null),
  offset_unit: z.literal("unicode_codepoint").optional().catch(undefined),
  text_precision: z
    .enum(["character", "phrase", "insertion", "page"])
    .optional()
    .catch(undefined),
  insertion_offset: z.number().int().nonnegative().nullish().catch(null),
  text_status: z
    .enum(["located", "missing", "ambiguous"])
    .optional()
    .catch(undefined),
  location_diagnostics: z.array(z.string()).catch([]),
  unlocated_reason: z.string().optional().catch(undefined),
});
const locationSchema = spanSchema.extend({
  id: z.string(),
  page: z.number(),
  boxes: z.array(z.unknown()).catch([]),
  image_precision: z.enum(["line", "region", "page"]).catch("page"),
  message: z.string().catch("定位信息不完整，请结合原文核对。"),
  source_reading: z.string().catch(""),
  candidate: z.string().catch(""),
  suggestion: z.string().catch(""),
  location: z.string().optional().catch(undefined),
  text_sha256: z.string().optional().catch(undefined),
  image_sha256: z.string().optional().catch(undefined),
  text_spans: z.array(z.unknown()).optional().catch(undefined),
});
const sourceBindings = new WeakMap<
  ReviewLocation,
  { text: string; sha?: string }
>();

/** API defaults belong at the transport boundary, while old saved locations remain readable. */
export function serializeReviewLocations(
  locations: ReviewLocation[],
): components["schemas"]["ReviewLocation"][] {
  const span = (
    value: ReviewTextSpan,
  ): components["schemas"]["ReviewTextSpan"] => ({
    ...value,
    text_status: value.text_status ?? "missing",
    text_precision: value.text_precision ?? "page",
    offset_unit: value.offset_unit ?? "unicode_codepoint",
    unlocated_reason: value.unlocated_reason ?? "",
  });
  return locations.map((location) => ({
    ...location,
    ...span(location),
    text_sha256: location.text_sha256 ?? "",
    image_sha256: location.image_sha256 ?? "",
    location: location.location ?? "",
    text_spans: location.text_spans?.map(span),
  }));
}

/** Keep a doubt when one optional image region is malformed; diagnostics explain the downgrade. */
export function readLocations(
  rows: unknown[],
  sourceText?: string,
  sourceSha?: string,
): ReviewLocation[] {
  return rows.flatMap((row) => {
    const parsed = locationSchema.safeParse(row);
    if (!parsed.success) return [];
    const data = parsed.data;
    const boxes = data.boxes.flatMap((box) => {
      const candidate = boxSchema.safeParse(box);
      return candidate.success ? [candidate.data] : [];
    });
    const diagnostics = [...data.location_diagnostics];
    if (
      !Array.isArray((row as Record<string, unknown>).boxes) ||
      boxes.length !== data.boxes.length
    )
      diagnostics.push("invalid_image_box");
    const exact = data.anchor.exact;
    const focusAt = exact.indexOf(data.focus);
    const validFocus = Boolean(
      data.focus && focusAt >= 0 && exact.indexOf(data.focus, focusAt + 1) < 0,
    );
    if (data.focus && !validFocus)
      diagnostics.push("invalid_focus_anchor_fallback");
    if (sourceSha && data.text_sha256 && sourceSha !== data.text_sha256)
      diagnostics.push("source_hash_mismatch");
    const raw = row as Record<string, unknown>;
    const rawAnchor = raw.anchor as Record<string, unknown> | undefined;
    if (
      ["start", "end", "insertion_offset"].some(
        (key) =>
          raw[key] != null &&
          (!Number.isInteger(raw[key]) || Number(raw[key]) < 0),
      ) ||
      (raw.offset_unit != null && raw.offset_unit !== "unicode_codepoint") ||
      (data.start != null && data.end != null && data.start > data.end) ||
      !rawAnchor ||
      ["exact", "prefix", "suffix"].some(
        (key) => typeof rawAnchor[key] !== "string",
      ) ||
      ["text_sha256", "image_sha256", "text_status", "text_precision"].some(
        (key) => raw[key] != null && typeof raw[key] !== "string",
      ) ||
      (raw.text_spans != null && !Array.isArray(raw.text_spans))
    )
      diagnostics.push("invalid_text_metadata");
    const text_spans = data.text_spans?.flatMap((span) => {
      const parsedSpan = spanSchema.safeParse(span);
      if (!parsedSpan.success) {
        diagnostics.push("invalid_text_span");
        return [];
      }
      const value = parsedSpan.data;
      return [
        {
          ...value,
          focus:
            value.focus && value.anchor.exact.includes(value.focus)
              ? value.focus
              : value.anchor.exact,
        },
      ];
    });
    const location: ReviewLocation = {
      ...data,
      boxes,
      text_spans,
      focus:
        data.insertion_offset != null ? "" : validFocus ? data.focus : exact,
      text_precision:
        data.insertion_offset != null
          ? "insertion"
          : !validFocus && exact
            ? "phrase"
            : data.text_precision,
      image_precision: boxes.length ? data.image_precision : "page",
      location_diagnostics: [...new Set(diagnostics)],
    };
    if (sourceText !== undefined)
      sourceBindings.set(location, { text: sourceText, sha: sourceSha });
    return [location];
  });
}

function literalRange(text: string, issue: ReviewTextSpan) {
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
    issue.insertion_offset != null &&
    issue.insertion_offset <= Array.from(exact).length
  ) {
    const at =
      starts[0] +
      Array.from(exact).slice(0, issue.insertion_offset).join("").length;
    return { start: at, end: at };
  }
  if (starts.length !== 1 || !exact) return null;
  if (!issue.focus || offset < 0 || exact.indexOf(issue.focus, offset + 1) >= 0)
    return { start: starts[0], end: starts[0] + exact.length };
  return {
    start: starts[0] + offset,
    end: starts[0] + offset + issue.focus.length,
  };
}

function boundRange(
  text: string,
  issue: ReviewTextSpan,
  owner: ReviewLocation,
) {
  if (issue.location_diagnostics?.includes("draft_location_unresolved"))
    return null;
  const binding = sourceBindings.get(owner);
  if (
    binding?.text === text &&
    Boolean(binding.sha && owner.text_sha256) &&
    binding.sha === owner.text_sha256 &&
    issue.offset_unit === "unicode_codepoint" &&
    issue.start != null &&
    issue.end != null &&
    issue.start <= issue.end &&
    issue.end <= Array.from(text).length
  ) {
    const start = Array.from(text).slice(0, issue.start).join("").length;
    const end = Array.from(text).slice(0, issue.end).join("").length;
    const { exact, prefix, suffix } = issue.anchor;
    for (let at = 0; exact && (at = text.indexOf(exact, at)) >= 0; at++) {
      if (
        at > start ||
        end > at + exact.length ||
        (prefix && !text.slice(0, at).endsWith(prefix)) ||
        (suffix && !text.slice(at + exact.length).startsWith(suffix))
      )
        continue;
      if (issue.insertion_offset != null) {
        const insertion =
          at +
          Array.from(exact).slice(0, issue.insertion_offset).join("").length;
        if (start === end && start === insertion) return { start, end };
      } else if (
        start < end &&
        text.slice(start, end) === (issue.focus || exact)
      )
        return { start, end };
    }
  }
  return literalRange(text, issue);
}

function resolveLocation(text: string, issue: ReviewLocation) {
  return boundRange(text, issue, issue);
}

export function sourceRanges(text: string, issue: ReviewLocation) {
  const spans = issue.text_spans?.length ? issue.text_spans : [issue];
  return spans.map((span) => boundRange(text, span, issue));
}

/** A full-anchor suggestion must retain the context outside the highlighted span. */
export function suggestionEdit(text: string, issue: ReviewLocation) {
  const range = resolveLocation(text, issue);
  if (!range || !issue.suggestion) return null;
  const exact = issue.anchor.exact;
  const relative =
    issue.insertion_offset != null
      ? Array.from(exact).slice(0, issue.insertion_offset).join("").length
      : exact.indexOf(issue.focus);
  if (
    relative < 0 ||
    (issue.insertion_offset == null &&
      (!issue.focus || exact.indexOf(issue.focus, relative + 1) >= 0))
  )
    return null;
  const before = exact.slice(0, relative);
  const after = exact.slice(
    relative + (issue.insertion_offset != null ? 0 : issue.focus.length),
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
    issue.insertion_offset == null
      ? issue.anchor.exact.indexOf(issue.focus)
      : Array.from(issue.anchor.exact).slice(0, issue.insertion_offset).join("")
          .length;
  if (at < 0) return null;
  const before = issue.anchor.prefix + issue.anchor.exact.slice(0, at);
  const after =
    issue.anchor.exact.slice(
      at + (issue.insertion_offset == null ? issue.focus.length : 0),
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
): ReviewLocation[] {
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
    if (!range) return recoverReviewLocations(before, after, [location])[0];
    let left = range.start,
      right = range.end;
    if (end <= left && start < left) {
      left += delta;
      right += delta;
    } else if (start >= right && !(left === right && start === left)) {
      /* position unchanged */
    } else if (start >= left && end <= right) {
      right += delta;
    } else return recoverReviewLocations(before, after, [location])[0];
    return relocated(
      location,
      after,
      left,
      right,
      (span) => moveReviewLocations(before, after, [span])[0],
    );
  });
}

function spanLocation(location: ReviewLocation, span: ReviewTextSpan) {
  const result = {
    ...location,
    ...span,
    text_spans: undefined,
    start: span.start ?? null,
    end: span.end ?? null,
    insertion_offset: span.insertion_offset ?? null,
    location_diagnostics: span.location_diagnostics ?? [],
    unlocated_reason: span.unlocated_reason ?? "",
  };
  const binding = sourceBindings.get(location);
  if (binding) sourceBindings.set(result, binding);
  return result;
}

function mapTextSpans(
  location: ReviewLocation,
  map: (span: ReviewLocation) => ReviewLocation,
): ReviewTextSpan[] | undefined {
  return location.text_spans?.map((span) => {
    const mapped = map(spanLocation(location, span));
    return {
      anchor: mapped.anchor,
      focus: mapped.focus,
      start: mapped.start,
      end: mapped.end,
      offset_unit: mapped.offset_unit,
      text_precision: mapped.text_precision,
      insertion_offset: mapped.insertion_offset,
      text_status: mapped.text_status,
      location_diagnostics: mapped.location_diagnostics,
      unlocated_reason: mapped.unlocated_reason,
    };
  });
}

function splitsCodePoint(text: string, at: number) {
  return (
    at > 0 &&
    at < text.length &&
    /[\uD800-\uDBFF]/.test(text[at - 1]) &&
    /[\uDC00-\uDFFF]/.test(text[at])
  );
}

function contextStart(text: string, at: number) {
  const bounded = Math.max(0, at);
  return splitsCodePoint(text, bounded) ? bounded - 1 : bounded;
}

function contextEnd(text: string, at: number) {
  const bounded = Math.min(text.length, at);
  return splitsCodePoint(text, bounded) ? bounded + 1 : bounded;
}

function relocated(
  location: ReviewLocation,
  text: string,
  left: number,
  right: number,
  mapSpan?: (span: ReviewLocation) => ReviewLocation,
): ReviewLocation {
  const a = contextStart(text, left - 32),
    b = contextEnd(text, right + 32);
  const {
    insertion_offset: _insertion,
    start: _start,
    end: _end,
    text_sha256: _sha,
    text_spans: spans,
    ...rest
  } = location;
  return {
    ...rest,
    focus: text.slice(left, right),
    text_status: "located",
    unlocated_reason: "",
    location_diagnostics: rest.location_diagnostics?.filter(
      (value) => value !== "draft_location_unresolved",
    ),
    start: null,
    end: null,
    anchor:
      left === right
        ? {
            exact: text.slice(a, b),
            prefix: text.slice(contextStart(text, a - 24), a),
            suffix: text.slice(b, contextEnd(text, b + 24)),
          }
        : {
            exact: text.slice(left, right),
            prefix: text.slice(a, left),
            suffix: text.slice(right, b),
          },
    text_precision:
      left === right
        ? "insertion"
        : Array.from(text.slice(left, right)).length === 1
          ? "character"
          : "phrase",
    ...(left === right
      ? { insertion_offset: Array.from(text.slice(a, left)).length }
      : {}),
    ...(spans && mapSpan
      ? {
          text_spans: mapTextSpans(location, mapSpan),
        }
      : {}),
  };
}

function uniqueAt(text: string, part: string) {
  const at = text.indexOf(part);
  return at >= 0 && text.indexOf(part, at + 1) < 0 ? at : null;
}

/** Old drafts may contain several edits; recover each independently from unchanged unique neighbours. */
export function recoverReviewLocations(
  before: string,
  after: string,
  locations: ReviewLocation[],
): ReviewLocation[] {
  if (before === after) return locations;
  return locations.map((location) => {
    const unresolved = (): ReviewLocation => ({
      ...location,
      text_status: "missing",
      start: null,
      end: null,
      location_diagnostics: [
        ...new Set([
          ...(location.location_diagnostics ?? []),
          "draft_location_unresolved",
        ]),
      ],
      text_spans: mapTextSpans(
        location,
        (span) => recoverReviewLocations(before, after, [span])[0],
      ),
    });
    const retained = resolveLocation(after, location);
    if (retained)
      return relocated(
        location,
        after,
        retained.start,
        retained.end,
        (span) => recoverReviewLocations(before, after, [span])[0],
      );
    const old = resolveLocation(before, location);
    if (!old) return unresolved();
    const left: number[] = old.start === 0 ? [0] : [];
    const right: number[] = old.end === before.length ? [after.length] : [];
    for (
      let size = 1;
      size <= Math.min(64, old.start) && !left.length;
      size++
    ) {
      if (splitsCodePoint(before, old.start - size)) continue;
      const context = before.slice(old.start - size, old.start);
      const at = uniqueAt(after, context);
      if (at !== null && uniqueAt(before, context) === old.start - size)
        left.push(at + context.length);
    }
    for (
      let size = 1;
      size <= Math.min(64, before.length - old.end) && !right.length;
      size++
    ) {
      if (splitsCodePoint(before, old.end + size)) continue;
      const context = before.slice(old.end, old.end + size);
      const at = uniqueAt(after, context);
      if (at !== null && uniqueAt(before, context) === old.end) right.push(at);
    }
    if (
      left.length === 1 &&
      right.length === 1 &&
      left[0] <= right[0] &&
      right[0] - left[0] <= Math.max(64, (old.end - old.start) * 8)
    )
      return relocated(
        location,
        after,
        left[0],
        right[0],
        (span) => recoverReviewLocations(before, after, [span])[0],
      );
    return unresolved();
  });
}

type BoundaryInsertion = { at: number; text: string };
export type ReviewRenderer = (
  markdown: string,
  insertions: BoundaryInsertion[],
) => string;
const defaultRenderer: ReviewRenderer = (raw) =>
  marked.parse(raw, { async: false });

/** Project a source range through the full rendering pipeline, without re-matching rendered phrases. */
export function projectSourceRange(
  joined: string,
  source: string,
  range: { start: number; end: number },
  render: ReviewRenderer,
  editorLayout = false,
) {
  const nonce = `HRSREVIEW${crypto.randomUUID().replaceAll("-", "")}`;
  const startToken = `${nonce}STARTTOKEN`,
    endToken = `${nonce}ENDTOKEN`;
  const insertions = [
    { at: range.start, text: startToken },
    { at: range.end, text: endToken },
  ];
  // Shadow markers preserve source order, including a zero-width insertion.
  const annotated =
    source.slice(0, range.start) +
    startToken +
    source.slice(range.start, range.end) +
    endToken +
    source.slice(range.end);
  const parsed = new DOMParser().parseFromString(
    render(annotated, insertions),
    "text/html",
  );
  const collect = (compact: boolean) => {
    const walker = document.createTreeWalker(parsed.body, NodeFilter.SHOW_TEXT);
    let node: Node | null,
      text = "",
      start: number | undefined,
      end: number | undefined;
    while ((node = walker.nextNode())) {
      let value = node.textContent ?? "";
      // ProseMirror omits serializer whitespace between structural blocks.
      if (
        compact &&
        /^\s*$/.test(value) &&
        /^(BODY|UL|OL|TABLE|THEAD|TBODY|TFOOT|TR|BLOCKQUOTE)$/.test(
          node.parentElement?.tagName ?? "",
        )
      )
        continue;
      const left = value.indexOf(startToken);
      if (left >= 0) {
        if (start !== undefined) return null;
        start = text.length + left;
        value = value.replace(startToken, "");
      }
      const right = value.indexOf(endToken);
      if (right >= 0) {
        if (end !== undefined) return null;
        end = text.length + right;
        value = value.replace(endToken, "");
      }
      text += value;
    }
    return text === joined &&
      start !== undefined &&
      end !== undefined &&
      start <= end &&
      (range.start === range.end || start < end)
      ? { start, end }
      : null;
  };
  return collect(false) ?? (editorLayout ? collect(true) : null);
}

export function renderedRanges(
  joined: string,
  source: string,
  issue: ReviewLocation,
  render: ReviewRenderer = defaultRenderer,
  editorLayout = false,
) {
  return sourceRanges(source, issue).map((range) =>
    range
      ? projectSourceRange(joined, source, range, render, editorLayout)
      : null,
  );
}

/** Decorate sanitized DOM text nodes; never splice markup into source Markdown. */
export function highlightDom(
  root: HTMLElement,
  text: string,
  issues: ReviewLocation[],
  active?: string,
  render: ReviewRenderer = defaultRenderer,
): string[] {
  for (const old of root.querySelectorAll<HTMLElement>("mark[data-review-id]"))
    old.dataset.reviewInsertion !== undefined
      ? old.remove()
      : old.replaceWith(...old.childNodes);
  root.normalize();
  const failures = new Set<string>();
  for (const issue of issues) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes: Text[] = [];
    let node: Node | null;
    while ((node = walker.nextNode()))
      if (!node.parentElement?.closest("mark[data-review-insertion]"))
        nodes.push(node as Text);
    const joined = nodes.map((n) => n.data).join("");
    const projectedRanges = renderedRanges(joined, text, issue, render);
    for (const projected of projectedRanges) {
      if (!projected) {
        failures.add(issue.id);
        continue;
      }
      // Earlier spans may have split text nodes. Rebuild against the unchanged displayed text.
      nodes.length = 0;
      const current = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      while ((node = current.nextNode()))
        if (!node.parentElement?.closest("mark[data-review-insertion]"))
          nodes.push(node as Text);
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
  }
  return [...failures];
}
