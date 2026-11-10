import { expect, test } from "vitest";
import { matchBasis } from "./match-basis";

const source = {
  title: "运输记录",
  text: "正文。",
};
test("match labels distinguish the displayed body from title, note and semantic relevance", () => {
  expect(matchBasis({ ...source, text: "高家堡违纪" }, "高家堡")).toBe(
    "原文片段含查询词",
  );
  expect(matchBasis({ ...source, title: "高家堡批语" }, "高家堡")).toBe(
    "标题或目录相关",
  );
  expect(
    matchBasis(
      {
        ...source,
        context: [{ text: "高家堡违纪" }],
      },
      "高家堡",
    ),
  ).toBe("关联注释或上下文含查询词");
  expect(matchBasis(source, "未见原词")).toBe("相关内容 · 请结合原文判断");
  expect(matchBasis(source, " ")).not.toBe("原文片段含查询词");
});
