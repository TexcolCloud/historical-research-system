// @vitest-environment happy-dom
import { afterEach, expect, test } from "vitest";
import { readResumeWork, rememberResumeWork } from "./workspace-resume";

afterEach(() => localStorage.clear());

test("the explicit continue action retains its full location only for the same workspace", () => {
  rememberResumeWork(
    "workspace-a",
    "/books/book?tab=reader&physicalPage=9&start=12&end=20&textSha=abc",
    "阅读：原件甲",
  );
  expect(readResumeWork("workspace-a")).toEqual({
    href: "/books/book?tab=reader&physicalPage=9&start=12&end=20&textSha=abc",
    title: "阅读：原件甲",
  });
  expect(readResumeWork("workspace-b")).toBeUndefined();
});

test("untrusted or credential-bearing locations cannot replace a useful retained location", () => {
  rememberResumeWork("workspace", "/timeline?topic=topic", "时间线索");
  expect(
    rememberResumeWork(
      "workspace",
      "/\\evil.invalid/arguments?topic=topic",
      "错误位置",
    ),
  ).toBeUndefined();
  expect(
    rememberResumeWork("workspace", "/books/book?token=private", "令牌位置"),
  ).toBeUndefined();
  expect(readResumeWork("workspace")?.href).toBe("/timeline?topic=topic");
  localStorage.setItem(
    "hrs:resume:workspace",
    JSON.stringify({
      workspace: "other-workspace",
      href: "/books/book",
      title: "另一个工作区",
    }),
  );
  expect(readResumeWork("workspace")).toBeUndefined();
});
