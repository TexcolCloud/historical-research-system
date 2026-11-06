import { projectSourceRange, type ReviewRenderer } from "./review-highlights";

export type ReadingAnnotation = {
  id: string;
  start: number;
  end: number;
  href?: string;
  label?: string;
};

/** Reading annotations cross the same full-render source mapping used by review highlights. */
export function annotateReading(
  root: HTMLElement,
  markdown: string,
  links: ReadingAnnotation[],
  focus: ReadingAnnotation | undefined,
  render: ReviewRenderer,
) {
  for (const old of root.querySelectorAll(
    "a[data-reading-link],mark[data-reading-focus]",
  ))
    old.replaceWith(...old.childNodes);
  root.normalize();
  const failures: string[] = [];
  const chars = Array.from(markdown);
  let focusNode: HTMLElement | null = null;
  for (const annotation of [...links, ...(focus ? [focus] : [])]) {
    let start = annotation.start,
      end = annotation.end;
    if (start < 0 || end > chars.length || end < start) {
      failures.push(annotation.id);
      continue;
    }
    // Paragraph separator whitespace is not a visible destination; putting a shadow
    // marker beyond it could change Markdown block structure.
    while (start < end && /\s/u.test(chars[start])) start++;
    while (end > start && /\s/u.test(chars[end - 1])) end--;
    const exact = chars.slice(start, end).join("");
    if (!exact) {
      failures.push(annotation.id);
      continue;
    }
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const leaves: Text[] = [];
    let node: Node | null;
    while ((node = walker.nextNode())) leaves.push(node as Text);
    const joined = leaves.map((n) => n.data).join("");
    const projected = projectSourceRange(
      joined,
      markdown,
      {
        start: chars.slice(0, start).join("").length,
        end: chars.slice(0, end).join("").length,
      },
      render,
    );
    if (!projected) {
      failures.push(annotation.id);
      continue;
    }
    let checkOffset = 0;
    const collides =
      annotation.href &&
      leaves.some((leaf) => {
        const next = checkOffset + leaf.length;
        const overlap = projected.start < next && projected.end > checkOffset;
        checkOffset = next;
        return overlap && Boolean(leaf.parentElement?.closest("a"));
      });
    if (collides) {
      failures.push(annotation.id);
      continue;
    }
    let offset = 0,
      decorated = false;
    for (const leaf of leaves) {
      const stop = offset + leaf.length;
      const left = Math.max(projected.start, offset) - offset;
      const right = Math.min(projected.end, stop) - offset;
      if (right > left) {
        const range = document.createRange();
        range.setStart(leaf, left);
        range.setEnd(leaf, right);
        const wrapper = document.createElement(annotation.href ? "a" : "mark");
        if (annotation.href) {
          wrapper.setAttribute("href", annotation.href);
          wrapper.dataset.readingLink = annotation.id;
          wrapper.className = "reading-toc-link";
          if (annotation.label)
            wrapper.setAttribute("aria-label", annotation.label);
        } else {
          wrapper.dataset.readingFocus = annotation.id;
          wrapper.className = "reading-target-highlight";
          wrapper.tabIndex = -1;
          wrapper.setAttribute("aria-label", "正文定位位置");
          focusNode ??= wrapper;
        }
        range.surroundContents(wrapper);
        decorated = true;
      }
      offset = stop;
    }
    if (!decorated) failures.push(annotation.id);
  }
  return { failures: [...new Set(failures)], focusNode };
}
