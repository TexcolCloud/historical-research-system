export type FootnoteLink = {
  id: string;
  label: string;
  note: { start: number; end: number; marker_end: number };
  references: { start: number; end: number }[];
};

const escape = (text: string) =>
  text.replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ]!,
  );

/** Render-only anchors: canonical source and backend codepoint offsets stay unchanged. */
export function withFootnotes(
  markdown: string,
  links: FootnoteLink[],
  prefix: string,
) {
  const chars = Array.from(markdown);
  const edits: { start: number; end: number; html: string }[] = [];
  for (const link of links) {
    const id = `${prefix}-${encodeURIComponent(link.id)}`,
      label = escape(link.label);
    const backlinks = link.references
      .map(
        (_, i) =>
          `<a href="#${id}-ref-${i}" data-note-jump aria-label="返回正文注号 ${label} ${i + 1}">↩${link.references.length > 1 ? i + 1 : ""}</a>`,
      )
      .join(" ");
    edits.push({
      start: link.note.start,
      end: link.note.marker_end,
      html: `<span id="${id}" tabindex="-1" class="footnote-target">${label}</span> ${backlinks} `,
    });
    link.references.forEach((ref, i) =>
      edits.push({
        ...ref,
        html: `<a id="${id}-ref-${i}" href="#${id}" data-note-jump aria-label="查看脚注 ${label}" class="footnote-reference">${escape(
          chars
            .slice(ref.start, ref.end)
            .join("")
            .replace(/^<sup>\s*|\s*<\/sup>$/gi, ""),
        )}</a>`,
      }),
    );
  }
  let end = chars.length;
  for (const edit of edits.sort((a, b) => b.start - a.start)) {
    if (edit.start < 0 || edit.end > end || edit.end <= edit.start)
      return markdown;
    chars.splice(edit.start, edit.end - edit.start, edit.html);
    end = edit.start;
  }
  return chars.join("");
}
