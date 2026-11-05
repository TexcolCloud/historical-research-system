import DOMPurify from "dompurify";
import { marked } from "marked";
import { useId, useMemo, useRef, useEffect } from "react";
import {
  highlightDom,
  type ReviewLocation,
  type ReviewRenderer,
} from "./review-highlights.ts";
import { withFootnotes, type FootnoteLink } from "@/editor/footnotes.ts";
const noFootnotes: FootnoteLink[] = [];

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
  footnotes = noFootnotes,
  assetBaseUrl,
  highlights,
  activeHighlight,
  onHighlight,
  onLocationFailure,
}: {
  markdown: string;
  footnotes?: FootnoteLink[];
  assetBaseUrl?: string;
  highlights?: ReviewLocation[];
  activeHighlight?: string;
  onHighlight?: (id: string) => void;
  onLocationFailure?: (ids: string[]) => void;
}) {
  const prefix = `footnotes-${useId()}`;
  const root = useRef<HTMLDivElement>(null);
  const failureCallback = useRef(onLocationFailure);
  const reportedFailures = useRef<string | undefined>(undefined);
  failureCallback.current = onLocationFailure;
  const renderSource = useMemo<ReviewRenderer>(
    () => (source, insertions) => {
      const edits = insertions.map((edit) => ({
        ...edit,
        at: Array.from(markdown.slice(0, edit.at)).length,
      }));
      const point = (at: number, right: boolean) =>
        at +
        edits.reduce(
          (sum, edit) =>
            sum +
            (edit.at < at || (right && edit.at === at) ? edit.text.length : 0),
          0,
        );
      const links = footnotes.map((link) => ({
        ...link,
        note: {
          start: point(link.note.start, true),
          end: point(link.note.end, false),
          marker_end: point(link.note.marker_end, false),
        },
        references: link.references.map((ref) => ({
          start: point(ref.start, true),
          end: point(ref.end, false),
        })),
      }));
      let reading = withFootnotes(source, links, prefix);
      if (assetBaseUrl)
        reading = reading.replace(
          /!\[([^\]]*)\]\([^\n]*?docling-assets[\\/]([\w.-]+)\)/g,
          (_, alt: string, name: string) =>
            `![${alt}](${assetBaseUrl}/docling-assets/${encodeURIComponent(name)})`,
        );
      if (assetBaseUrl)
        reading = reading.replace(
          /!\[([^\]]*)\]\((pages\/\d+\.png|evidence\/details\/[\w.-]+\.png)\)/g,
          (_, alt: string, path: string) =>
            `![${alt}](${assetBaseUrl}/${path})`,
        );
      return safeHtml(reading);
    },
    [markdown, footnotes, prefix, assetBaseUrl],
  );
  const html = useMemo(
    () => renderSource(markdown, []),
    [markdown, renderSource],
  );
  useEffect(() => {
    if (root.current) {
      const failures = highlightDom(
        root.current,
        markdown,
        highlights ?? [],
        activeHighlight,
        renderSource,
      );
      const signature = JSON.stringify(failures);
      if (reportedFailures.current !== signature) {
        reportedFailures.current = signature;
        failureCallback.current?.(failures);
      }
    }
  }, [html, markdown, highlights, activeHighlight, renderSource]);
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
