import DOMPurify from "dompurify";
import { marked } from "marked";
import { useId, useMemo } from "react";
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
}: {
  markdown: string;
  footnotes?: FootnoteLink[];
  assetBaseUrl?: string;
}) {
  const prefix = `footnotes-${useId()}`;
  const html = useMemo(() => {
    let reading = withFootnotes(markdown, footnotes, prefix);
    if (assetBaseUrl)
      reading = reading.replace(
        /!\[([^\]]*)\]\([^\n]*?docling-assets[\\/]([\w.-]+)\)/g,
        (_, alt: string, name: string) =>
          `![${alt}](${assetBaseUrl}/docling-assets/${encodeURIComponent(name)})`,
      );
    return safeHtml(reading);
  }, [markdown, footnotes, prefix, assetBaseUrl]);
  return (
    <div
      className="prose"
      onClick={(event) => {
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
