// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as libraryApi from "@/client/api";
import BookScope from "./BookScope";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
test("book selection inside question creation never submits the enclosing question form", async () => {
  vi.spyOn(libraryApi, "readBooks").mockResolvedValue([]);
  const submit = vi.fn();
  const apply = vi.fn();
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <BookScope value={[]} onApply={apply} />
      </form>
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole("button", { name: "选择书籍" }));
  expect(submit).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "应用书籍范围" }));
  expect(apply).toHaveBeenCalledWith([]);
  expect(submit).not.toHaveBeenCalled();
});
