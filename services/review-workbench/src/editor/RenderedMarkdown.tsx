import DOMPurify from "dompurify";
import { marked } from "marked";
import { useId, useMemo, useRef, useEffect } from "react";
import { highlightDom, type ReviewLocation } from "./review-highlights.ts";
import { withFootnotes, type FootnoteLink } from "@/editor/footnotes.ts";

function safeHtml(markdown: string) {
  return DOMPurify.sanitize(marked.parse(markdown, { async: false }), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["style", "iframe", "form", "input", "button"],
    FORBID_ATTR: ["style"],
    ADD_ATTR: ["rowspan", "colspan"],
  });
}
export function RenderedMarkdown({
  markdown,
  footnotes = [],
  assetBaseUrl,
  highlights,
  activeHighlight,
  onHighlight,
}: {
  markdown: string;
  footnotes?: FootnoteLink[];
  assetBaseUrl?: string;
  highlights?: ReviewLocation[];
  activeHighlight?: string;
  onHighlight?: (id: string) => void;
}) {
  const prefix = `footnotes-${useId()}`;
  const root = useRef<HTMLDivElement>(null);
  const html = useMemo(() => {
    let reading = withFootnotes(markdown, footnotes, prefix);
    if (assetBaseUrl)
      reading = reading.replace(
        /!\[([^\]]*)\]\([^\n]*?docling-assets[\\/]([\w.-]+)\)/g,
        (_, alt: string, name: string) =>
          `![${alt}](${assetBaseUrl}/docling-assets/${encodeURIComponent(name)})`,
      );
    if (assetBaseUrl)
      reading = reading.replace(
        /!\[([^\]]*)\]\((pages\/\d+\.png|evidence\/details\/[\w.-]+\.png)\)/g,
        (_, alt: string, path: string) => `![${alt}](${assetBaseUrl}/${path})`,
      );
    return safeHtml(reading);
  }, [markdown, footnotes, prefix, assetBaseUrl]);
  useEffect(() => {
    if (root.current)
      highlightDom(root.current, markdown, highlights ?? [], activeHighlight);
  }, [html, markdown, highlights, activeHighlight]);
  return (
    <div
      className="prose"
      ref={root}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        const mark = (event.target as Element).closest<HTMLElement>(
          "mark[data-review-id]",
        );
        if (mark?.dataset.reviewId) {
          event.preventDefault();
          onHighlight?.(mark.dataset.reviewId);
        }
      }}
      onClick={(event) => {
        const mark = (event.target as Element).closest<HTMLElement>(
          "mark[data-review-id]",
        );
        if (mark?.dataset.reviewId) onHighlight?.(mark.dataset.reviewId);
        const link = (event.target as Element).closest<HTMLAnchorElement>(
          "a[data-note-jump]",
        );
        if (!link) return;
        const id = link.getAttribute("href")?.slice(1);
        const target =
          id &&
          event.currentTarget.querySelector<HTMLElement>(
            `[id="${CSS.escape(id)}"]`,
          );
        if (!target) return;
        // HashRouter owns the URL fragment; footnote jumps stay inside this document.
        event.preventDefault();
        target.scrollIntoView({ block: "center" });
        target.focus({ preventScroll: true });
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
