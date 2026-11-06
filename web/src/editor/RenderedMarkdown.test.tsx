// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RenderedMarkdown } from "@/editor/RenderedMarkdown.tsx";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("footnote and backlink scroll within the reader without changing the HashRouter URL", async () => {
  window.location.hash = "/books/book?tab=reader";
  const before = window.location.hash;
  const scroll = vi
    .spyOn(HTMLElement.prototype, "scrollIntoView")
    .mockImplementation(() => {});
  const markdown = "𠮷地记载①。\n\n① 不包括乙地。";
  render(
    <RenderedMarkdown
      markdown={markdown}
      footnotes={[
        {
          id: "note",
          label: "①",
          note: { start: 8, end: 16, marker_end: 9 },
          references: [{ start: 4, end: 5 }],
        },
      ]}
    />,
  );
  const ref = screen.getByRole("link", { name: "查看脚注 ①" });
  expect(ref.textContent).toBe("①");
  await userEvent.click(ref);
  expect(scroll).toHaveBeenCalledOnce();
  expect(window.location.hash).toBe(before);
  expect(document.activeElement?.textContent).toBe("①");
  await userEvent.click(screen.getByRole("link", { name: "返回正文注号 ① 1" }));
  expect(document.activeElement).toBe(ref);
  expect(window.location.hash).toBe(before);
});

test("separate markdown panes do not reuse note anchor IDs", () => {
  const props = {
    markdown: "正文[^a]。\n\n[^a]: 注释。",
    footnotes: [
      {
        id: "a",
        label: "a",
        note: { start: 9, end: 18, marker_end: 14 },
        references: [{ start: 2, end: 6 }],
      },
    ],
  };
  render(
    <>
      <RenderedMarkdown {...props} />
      <RenderedMarkdown {...props} />
    </>,
  );
  const links = screen.getAllByRole("link", { name: "查看脚注 a" });
  expect(links[0].getAttribute("href")).not.toBe(links[1].getAttribute("href"));
});

test("preserved document pictures load through the S3 artifact API instead of local Windows paths", () => {
  render(
    <RenderedMarkdown
      markdown={"![地图](D:\\private cache\\docling-assets\\map.png)"}
      assetBaseUrl="/api/v2/runs/run/artifacts"
    />,
  );
  expect(screen.getByAltText("地图").getAttribute("src")).toBe(
    "/api/v2/runs/run/artifacts/docling-assets/map.png",
  );
});
