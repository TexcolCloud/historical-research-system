import DOMPurify from "dompurify";
import { marked } from "marked";

function safeHtml(markdown: string) {
  return DOMPurify.sanitize(marked.parse(markdown, { async: false }), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["style", "iframe", "form", "input", "button"],
    FORBID_ATTR: ["style"],
    ADD_ATTR: ["rowspan", "colspan"],
  });
}
export function RenderedMarkdown({ markdown }: { markdown: string }) {
  return (
    <div
      className="prose"
      dangerouslySetInnerHTML={{ __html: safeHtml(markdown) }}
    />
  );
}
