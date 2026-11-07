// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DOMPurify from "dompurify";
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

test("adjacent footnote definitions remain separate reading paragraphs", () => {
  const markdown =
    "𠮷地正文[^a]、[^b]、[^c]。\n\n[^a]: 第一条。\n[^b]: 第二条。\n[^c]: 第三条。";
  const at = (fragment: string) =>
    Array.from(markdown.slice(0, markdown.indexOf(fragment))).length;
  const labels = ["a", "b", "c"];
  const { container } = render(
    <RenderedMarkdown
      markdown={markdown}
      footnotes={labels.map((label, index) => {
        const marker = `[^${label}]`;
        return {
          id: label,
          label,
          note: {
            start: at(`${marker}:`),
            marker_end: at(`${marker}:`) + marker.length + 1,
            end:
              index + 1 < labels.length
                ? at(`[^${labels[index + 1]}]:`)
                : Array.from(markdown).length,
          },
          references: [{ start: at(marker), end: at(marker) + marker.length }],
        };
      })}
    />,
  );
  const targets = [...container.querySelectorAll(".footnote-target")];
  const paragraphs = targets.map((target) => target.closest("p"));
  expect(targets).toHaveLength(3);
  expect(paragraphs.every(Boolean)).toBe(true);
  expect(new Set(paragraphs).size).toBe(3);
  ["第一条。", "第二条。", "第三条。"].forEach((text, index) =>
    expect(paragraphs[index]?.textContent).toContain(text),
  );
});

test("separate definitions preserve continued bodies, repeated labels and every backlink safely", () => {
  const sanitize = vi.spyOn(DOMPurify, "sanitize");
  const markdown =
    '𠮷地甲[^1]，再次[^1]。乙地[^1]。\n\n[^1]: 第一条。\n    续行含**重点**。\n[^1]: 第二条<img src="/note.png" alt="注释图" onerror="alert(1)">。';
  const positions = Array.from(
    markdown.matchAll(/\[\^1\]/g),
    (match) => Array.from(markdown.slice(0, match.index)).length,
  );
  const footnotes = [
    {
      id: "first-page-note",
      label: "1",
      note: {
        start: positions[3],
        marker_end: positions[3] + 5,
        end: positions[4],
      },
      references: positions
        .slice(0, 2)
        .map((start) => ({ start, end: start + 4 })),
    },
    {
      id: "second-page-note",
      label: "1",
      note: {
        start: positions[4],
        marker_end: positions[4] + 5,
        end: Array.from(markdown).length,
      },
      references: [{ start: positions[2], end: positions[2] + 4 }],
    },
  ];
  const before = structuredClone(footnotes);
  const { container } = render(
    <RenderedMarkdown markdown={markdown} footnotes={footnotes} />,
  );
  const references = screen.getAllByRole("link", { name: "查看脚注 1" });
  expect(references).toHaveLength(3);
  expect(references[0].getAttribute("href")).toBe(
    references[1].getAttribute("href"),
  );
  expect(references[0].getAttribute("href")).not.toBe(
    references[2].getAttribute("href"),
  );
  const targets = [...container.querySelectorAll(".footnote-target")];
  const first = targets[0].closest("p");
  const second = targets[1].closest("p");
  expect(first).not.toBe(second);
  expect(first?.textContent).toContain("续行含重点。");
  expect(first?.querySelector("strong")?.textContent).toBe("重点");
  expect(first?.querySelectorAll("a[data-note-jump]")).toHaveLength(2);
  expect(second?.querySelectorAll("a[data-note-jump]")).toHaveLength(1);
  // DOMPurify does not support happy-dom as a security harness; retain its delegation/config.
  expect(sanitize).toHaveBeenCalledWith(
    expect.stringContaining('onerror="alert(1)"'),
    expect.objectContaining({
      USE_PROFILES: { html: true },
      FORBID_TAGS: ["style", "iframe", "form", "input", "button"],
      FORBID_ATTR: ["style"],
    }),
  );
  expect(footnotes).toEqual(before);
});

test("rendered footnote labels escape markup before sanitization", () => {
  const label = '<img src=x onerror="alert(1)">';
  const { container } = render(
    <RenderedMarkdown
      markdown="正文[^a]。\n\n[^a]: 注释。"
      footnotes={[
        {
          id: "a",
          label,
          note: { start: 9, end: 18, marker_end: 14 },
          references: [{ start: 2, end: 6 }],
        },
      ]}
    />,
  );
  expect(container.querySelector(".footnote-target")?.textContent).toBe(label);
  expect(container.querySelector("img")).toBeNull();
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
