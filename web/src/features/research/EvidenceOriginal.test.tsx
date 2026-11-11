// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import * as libraryApi from "@/client/api";
import EvidenceOriginal from "./EvidenceOriginal";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("card-local proof IDs cannot select another source or carry its page into that source", async () => {
  const seen: string[] = [];
  vi.spyOn(libraryApi, "readOriginalPage").mockImplementation(
    async (run, page) => {
      seen.push(`${run}/${page}`);
      return `/source-${run}-${page}.png`;
    },
  );
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <EvidenceOriginal
          selected="e1"
          evidence={[
            {
              id: "e1",
              pages: [9, 10],
              reading_target: { run_id: "first", physical_page: 9 },
            },
            {
              id: "e1",
              pages: [2, 3],
              reading_target: { run_id: "second", physical_page: 2 },
            },
          ]}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByRole("img", { name: "PDF 第 9 页" });
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "原件页码" }),
    "10",
  );
  await screen.findByRole("img", { name: "PDF 第 10 页" });
  const choices = screen.getByRole("combobox", { name: "原件证据" });
  await userEvent.selectOptions(
    choices,
    screen.getByRole("option", { name: "证据 2" }),
  );
  await screen.findByRole("img", { name: "PDF 第 2 页" });
  expect(seen.at(-1)).toBe("second/2");
  expect(seen).not.toContain("second/10");
  expect(
    screen.getByRole("link", { name: "打开原 PDF" }).getAttribute("href"),
  ).toContain("/runs/second/");
});
test("evidence opens its retained source run and lets the reader change page and zoom without leaving the card", async () => {
  const seen: string[] = [];
  vi.spyOn(libraryApi, "readOriginalPage").mockImplementation(
    async (runId, page) => {
      seen.push(`${runId}/${page}`);
      return "/source-page.png";
    },
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <p>正在核对的卡片</p>
        <EvidenceOriginal
          evidence={[
            {
              id: "e",
              pages: [9, 10],
              reading_target: { run_id: "retained-run", physical_page: 9 },
            },
          ]}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(screen.getByRole("button", { name: "对照原件" }));
  await screen.findByRole("img", { name: "PDF 第 9 页" });
  expect(seen).toEqual(["retained-run/9"]);
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "原件页码" }),
    "10",
  );
  await screen.findByRole("img", { name: "PDF 第 10 页" });
  expect(seen.at(-1)).toBe("retained-run/10");
  expect(screen.getByText("正在核对的卡片")).toBeTruthy();
  expect(screen.getByRole("slider", { name: "原图放大倍数" })).toBeTruthy();
});
