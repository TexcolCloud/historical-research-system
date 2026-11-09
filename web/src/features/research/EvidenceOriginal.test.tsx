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
