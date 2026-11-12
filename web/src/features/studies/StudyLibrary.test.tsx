// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { respondWith } from "@/test/responses";
import StudyLibrary from "./StudyLibrary";

vi.mock("@/features/research/BookResearch", () => ({
  default: () => <p>另一本书的史料卡</p>,
}));
afterEach(cleanup);

test("a missing explicit card source never silently opens a different book", async () => {
  respondWith(() => [{ id: "other-book", title: "另一本书", state: "ready" }]);
  render(
    <MemoryRouter initialEntries={["/library?view=cards&book_id=missing-book"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <StudyLibrary />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "所选书籍不在当前工作区，请重新选择来源书籍。",
  );
  expect(screen.queryByText("另一本书的史料卡")).toBeNull();
});
