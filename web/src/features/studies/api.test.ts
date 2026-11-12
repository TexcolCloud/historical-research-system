import { afterEach, expect, test, vi } from "vitest";
import { studies, StudyError } from "./api";

afterEach(() => vi.unstubAllGlobals());

test("export links use the HTTP route's markdown and json format values", () => {
  expect(studies.exportURL("canvas", "md")).toBe(
    "/api/v2/studies/canvases/canvas/export?format=markdown",
  );
  expect(studies.exportURL("canvas", "json")).toBe(
    "/api/v2/studies/canvases/canvas/export?format=json",
  );
});

test("a version conflict retains the server explanation and never retries a mutation", async () => {
  const fetcher = vi.fn(async () =>
    Response.json(
      { detail: "画布已有新版本，请保留当前输入。" },
      { status: 409 },
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  await expect(
    studies.updateCanvas("canvas", { expected_version: 2, title: "整理" }),
  ).rejects.toMatchObject({
    name: "Error",
    status: 409,
    message: "画布已有新版本，请保留当前输入。",
  });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(StudyError).toBeDefined();
});

test("a structured validation error exposes the actionable source-date explanation", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json(
        {
          detail: [
            {
              loc: ["body", "date"],
              msg: "Value error, 规范日期无效；请保留原日期文字。",
            },
          ],
        },
        { status: 422 },
      ),
    ),
  );
  await expect(
    studies.createRecord("topic", { kind: "clue", title: "核对日期" }),
  ).rejects.toThrow("规范日期无效；请保留原日期文字。");
});
