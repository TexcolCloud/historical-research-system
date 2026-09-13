import { expect, test } from "vitest";
import { splitMarkdown } from "./markdown.ts";

test("splitting keeps Markdown, embedded HTML and uncommon Unicode exact", () => {
  const original =
    '# 标题\r\n\r\n正文  𠀀。\r\n\r\n<table><tr><td rowspan="2">資料</td><td>x</td></tr><tr><td>y</td></tr></table>\r\n\r\n<!-- 留存 -->\r\n';
  const blocks = splitMarkdown(original);
  expect(blocks.map((block) => block.raw).join("")).toBe(original);
  const index = blocks.findIndex((block) => block.raw.startsWith("正文"));
  expect(index).toBeGreaterThanOrEqual(0);
});
