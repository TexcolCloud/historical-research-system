// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { respondWith } from "@/test/responses.ts";
import SearchPanel from "@/features/search/SearchPanel.tsx";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("search preserves the hit and exposes separately cited context and its chapter link", async () => {
  let requested = "";
  respondWith((request) => {
    requested = request.url;
    return [
      {
        id: "hit",
        book_id: "book",
        run_id: "run",
        chapter_id: "chapter2",
        title: "运输",
        text: "登记120吨",
        pages: [8],
        start: 30,
        end: 36,
        score: 1,
        section_path: ["运输", "甲地"],
        context_truncated: true,
        context: [
          {
            start: 100,
            end: 108,
            text: "不包括乙地",
            pages: [9],
            role: "footnote",
          },
        ],
      },
    ];
  });
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <SearchPanel bookId="book" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.type(screen.getByLabelText("检索本书正文"), "粮食");
  await userEvent.click(screen.getByRole("button", { name: "查找" }));
  await screen.findByText("登记120吨");
  expect(new URL(requested).searchParams.get("semantic")).toBe("true");
  await userEvent.click(screen.getByLabelText("研究检索（关闭后快速查词）"));
  await userEvent.click(screen.getByLabelText("跨章节综合"));
  await userEvent.click(screen.getByRole("button", { name: "查找" }));
  await vi.waitFor(() =>
    expect(new URL(requested).searchParams.get("semantic")).toBe("false"),
  );
  expect(new URL(requested).searchParams.get("diverse")).toBe("true");
  await userEvent.click(screen.getByText("查看相关上下文、表头与注释"));
  expect(
    screen.getByRole("link", { name: "查看原件第 9 页" }).getAttribute("href"),
  ).toContain("#page=9");
  expect(
    screen.getByRole("link", { name: "阅读所在章节" }).getAttribute("href"),
  ).toBe("/books/book?tab=reader&chapter=chapter2");
  expect(screen.getByText("不包括乙地")).toBeTruthy();
});
