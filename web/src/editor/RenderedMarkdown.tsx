import DOMPurify from "dompurify";
import { marked } from "marked";
import { useId, useMemo, useRef, useEffect } from "react";
import {
  highlightDom,
  type ReviewLocation,
  type ReviewRenderer,
} from "./review-highlights.ts";
import { withFootnotes, type FootnoteLink } from "@/editor/footnotes.ts";
import { annotateReading, type ReadingAnnotation } from "./reading-annotations";
const noFootnotes: FootnoteLink[] = [];
const noReadingLinks: ReadingAnnotation[] = [];

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
  readingLinks = noReadingLinks,
  readingFocus,
  onReadingFailure,
  onReadingNavigate,
}: {
  markdown: string;
  footnotes?: FootnoteLink[];
  assetBaseUrl?: string;
  highlights?: ReviewLocation[];
  activeHighlight?: string;
  onHighlight?: (id: string) => void;
  onLocationFailure?: (ids: string[]) => void;
  readingLinks?: ReadingAnnotation[];
  readingFocus?: ReadingAnnotation;
  onReadingFailure?: (ids: string[]) => void;
  onReadingNavigate?: (href: string) => void;
}) {
  const prefix = `footnotes-${useId()}`;
  const root = useRef<HTMLDivElement>(null);
  const failureCallback = useRef(onLocationFailure);
  const reportedFailures = useRef<string | undefined>(undefined);
  const readingFailureCallback = useRef(onReadingFailure);
  const reportedReadingFailures = useRef<string | undefined>(undefined);
  const focusedReading = useRef<string | undefined>(undefined);
  const renderedReadingSource = useRef<string | undefined>(undefined);
  failureCallback.current = onLocationFailure;
  readingFailureCallback.current = onReadingFailure;
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
  // A background annotation refresh must not replace an identical anchor between
  // pointer-down and click. Values, rather than response-array identity, own DOM updates.
  const readingLinksKey = JSON.stringify(readingLinks);
  const readingFocusKey = JSON.stringify(readingFocus);
  const highlightsKey = JSON.stringify(highlights);
  useEffect(() => {
    if (root.current) {
      const readingSourceChanged = renderedReadingSource.current !== html;
      renderedReadingSource.current = html;
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
      const reading = annotateReading(
        root.current,
        markdown,
        readingLinks,
        readingFocus,
        renderSource,
      );
      const readingSignature = JSON.stringify(reading.failures);
      if (reportedReadingFailures.current !== readingSignature) {
        reportedReadingFailures.current = readingSignature;
        readingFailureCallback.current?.(reading.failures);
      }
      if (!readingFocus) focusedReading.current = undefined;
      if (
        reading.focusNode &&
        (focusedReading.current !== readingFocus?.id || readingSourceChanged)
      ) {
        focusedReading.current = readingFocus?.id;
        reading.focusNode.scrollIntoView?.({ block: "center" });
        reading.focusNode.focus({ preventScroll: true });
      }
    }
  }, [
    html,
    markdown,
    highlightsKey,
    activeHighlight,
    readingLinksKey,
    readingFocusKey,
  ]);
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
        const readingLink = (
          event.target as Element
        ).closest<HTMLAnchorElement>("a[data-reading-link]");
        if (
          readingLink &&
          onReadingNavigate &&
          !event.ctrlKey &&
          !event.metaKey &&
          !event.shiftKey &&
          !event.altKey
        ) {
          const href = readingLink.getAttribute("href");
          if (href) {
            event.preventDefault();
            onReadingNavigate(href);
            return;
          }
        }
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
