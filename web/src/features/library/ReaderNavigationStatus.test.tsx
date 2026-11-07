// @vitest-environment happy-dom
import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ReaderPanel from "./ReaderPanel";
import { respondWith } from "@/test/responses";
import { navigationCompleted } from "./navigation-status.ts";

afterEach(cleanup);

test("reading distinguishes final no-link, automatic waiting, pending review and a display-only mapping gap", async () => {
  const make = (id: string, status: string, needs_confirmation: boolean) => ({
    id,
    title: id,
    status,
    needs_confirmation,
    level: 1,
    path: [id],
    target_label: "4",
    source_page: 1,
    source_anchor: `${id} 4`,
    source_image: "/toc.png",
    reason: "",
    reading_target: null,
  });
  const chapter = {
    id: "chapter",
    book_id: "book",
    run_id: "run",
    position: 0,
    title: "正式正文",
    kind: "article",
    pages: [2],
    codepoints: 4,
    text: "正式正文",
    reading_text: "正式正文",
    text_sha256: "a".repeat(64),
    parts: [],
    footnotes: [],
    reading_footnotes: [],
    structure: [],
    reading_segments: [],
    toc_links: [],
  };
  respondWith((request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/reading-navigation"))
      return {
        state: "available",
        version: 1,
        sha256: "b".repeat(64),
        pending_count: 1,
        entries: [
          make("已由人工确认的条目", "unlinked_manual", false),
          make("仍在核验的条目", "waiting_evidence", false),
          make("未找到正文的条目", "not_found", true),
          {
            ...make("已关联但阅读版无映射", "matched", false),
            target_image: "/verified-original.png",
          },
          {
            ...make("机器原图核验的条目", "matched_verified", false),
            target_image: "/machine-verified-original.png",
          },
          {
            ...make("机器核验可阅读", "matched_verified", false),
            reading_target: {
              chapter_id: "chapter",
              run_id: "run",
              physical_page: 2,
              start: 0,
              end: 4,
              text_sha256: "a".repeat(64),
              image_sha256: "b".repeat(64),
              anchor: "正式正文",
            },
          },
        ],
      };
    if (path.endsWith("/chapters")) return [chapter];
    return chapter;
  });
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <ReaderPanel bookId="book" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const completed = (await screen.findByText("已由人工确认的条目")).closest(
    "li",
  )!;
  expect(completed.textContent).toContain("已确认不设置跳转");
  expect(
    within(completed).queryByRole("link", { name: "核对位置" }),
  ).toBeNull();
  const waiting = screen.getByText("仍在核验的条目").closest("li")!;
  expect(waiting.textContent).toContain("等待系统核验");
  expect(within(waiting).queryByRole("link", { name: "核对位置" })).toBeNull();
  const pending = screen.getByText("未找到正文的条目").closest("li")!;
  expect(pending.textContent).toContain("需要你确认");
  expect(
    within(pending)
      .getByRole("link", { name: "核对位置" })
      .getAttribute("href"),
  ).toContain("entry=");
  const display = screen.getByText("已关联但阅读版无映射").closest("li")!;
  expect(display.textContent).toContain("已关联正文");
  expect(
    within(display)
      .getByRole("link", { name: "查看对应原图" })
      .getAttribute("href"),
  ).toBe("/verified-original.png");
  expect(within(display).queryByRole("link", { name: "核对位置" })).toBeNull();
  const verified = screen.getByText("机器原图核验的条目").closest("li")!;
  expect(verified.textContent).toContain("原图核验已关联");
  expect(within(verified).queryByRole("link", { name: "核对位置" })).toBeNull();
  expect(
    screen.getByRole("link", { name: /机器核验可阅读/ }).getAttribute("href"),
  ).toContain("physicalPage=2");
  expect(navigationCompleted(make("machine", "matched_verified", false))).toBe(
    true,
  );
  expect(screen.queryByText(/暂无可靠正文位置/)).toBeNull();
});
