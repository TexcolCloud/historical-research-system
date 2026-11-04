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
