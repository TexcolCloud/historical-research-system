// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import DeleteBookButton from "@/features/books/DeleteBookButton.tsx";
import { booksKey, applyBookEvent } from "@/hooks/events.ts";
import { respondWith } from "@/test/responses.ts";
import type { Book } from "@/client/api.ts";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
test.each(["queued", "uploading"])(
  "%s book can be deleted through confirmation and cleanup events",
  async (state) => {
    const book: Book = {
      id: "test",
      title: "技术夹具",
      state,
      run_id: "run",
      stage: "verify_upload",
      revision: 0,
      created_at: "2026-09-13",
    };
    const query = new QueryClient();
    query.setQueryData(booksKey, [book]);
    const calls: string[] = [];
    respondWith((request) => {
      calls.push(request.method);
      return { book_id: "test", state: "pending", error: null };
    });
    render(
      <QueryClientProvider client={query}>
        <DeleteBookButton book={book} />
      </QueryClientProvider>,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "删除任务：技术夹具" }),
    );
    expect(calls).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    expect(calls).toEqual(["DELETE"]);
    expect(query.getQueryData<Book[]>(booksKey)?.[0].state).toBe("deleting");
    applyBookEvent(
      query,
      JSON.stringify({
        sequence: 10,
        book_id: "test",
        run_id: null,
        kind: "book.deleted",
        payload: { state: "deleted" },
      }),
    );
    expect(query.getQueryData(booksKey)).toEqual([]);
  },
);
