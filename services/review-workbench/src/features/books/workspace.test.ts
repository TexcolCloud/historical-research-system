import { expect, test } from "vitest";
import { bookWorkspaceHref } from "./workspace.ts";

test("book entry follows the same human responsibility from every shelf or task entry", () => {
  expect(
    bookWorkspaceHref({
      id: "mixed",
      state: "running",
      pending_count: 1,
      navigation_pending_count: 2,
    }),
  ).toBe("/books/mixed?tab=review");
  expect(
    bookWorkspaceHref({
      id: "directory",
      state: "ready",
      pending_count: 0,
      navigation_pending_count: 1,
    }),
  ).toBe("/books/directory?tab=review&task=navigation");
  expect(bookWorkspaceHref({ id: "reading", state: "ready" })).toBe(
    "/books/reading?tab=reader",
  );
  expect(bookWorkspaceHref({ id: "wait", state: "queued" })).toBe(
    "/books/wait?tab=progress",
  );
});
