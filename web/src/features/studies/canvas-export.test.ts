// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { exportCanvasImage } from "./canvas-export";

afterEach(() => vi.restoreAllMocks());

test("portable SVG renders source labels as text and keeps only placement references in metadata", async () => {
  const captured: Blob[] = [];
  vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
    if (blob instanceof Blob) captured.push(blob);
    return "blob:study-export";
  });
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  const dangerous = '<script>alert("source")</script> & 原文';
  await exportCanvasImage(
    {
      nodes: [
        {
          id: "n",
          record_kind: "source",
          record_id: "membership",
          x: 10,
          y: 20,
          width: 280,
          height: 250,
        },
      ],
      edges: [],
      viewport: { x: 0, y: 0, z: 1 },
    },
    new Map([
      [
        "n",
        { title: dangerous, kind: "史料", text: "预览内容", meta: "来源卡 v2" },
      ],
    ]),
    "研究/画布",
    "svg",
  );
  const svg = await captured[0].text();
  const parsed = new DOMParser().parseFromString(svg, "image/svg+xml");
  expect(parsed.querySelector("script")).toBeNull();
  expect(parsed.querySelector("parsererror")).toBeNull();
  expect(parsed.documentElement.textContent).toContain("来源卡 v2");
  expect(parsed.querySelector("metadata")?.textContent).toContain(
    '"record_id":"membership"',
  );
  expect(click).toHaveBeenCalledOnce();
});
