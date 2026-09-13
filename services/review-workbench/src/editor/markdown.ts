import { marked } from "marked";

export type MarkdownBlock = { raw: string; type: string };
export function splitMarkdown(markdown: string): MarkdownBlock[] {
  // Marked normalizes line endings. Map its offsets back to the untouched source.
  const boundaries = [0];
  let normalized = "";
  for (let at = 0; at < markdown.length; at++) {
    const char = markdown[at];
    if (char === "\r") {
      if (markdown[at + 1] === "\n") at++;
      normalized += "\n";
    } else normalized += char;
    boundaries.push(at + 1);
  }
  const tokens = marked.lexer(normalized);
  if (tokens.map((token) => token.raw).join("") !== normalized)
    return [{ raw: markdown, type: "source" }];
  let offset = 0;
  return tokens.map((token) => {
    const start = offset;
    offset += token.raw.length;
    return {
      raw: markdown.slice(boundaries[start], boundaries[offset]),
      type: token.type,
    };
  });
}
