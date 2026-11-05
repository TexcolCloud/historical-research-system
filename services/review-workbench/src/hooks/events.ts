import type { QueryClient } from "@tanstack/react-query";
import { z } from "zod";
import type { Book } from "@/client/api.ts";

import { queryKeys } from "@/client/query-keys.ts";
import { updateBooks } from "@/features/books/book-cache.ts";
export const booksKey = queryKeys.books;
export function patchBookDeletion(
  query: QueryClient,
  bookId: string,
  state: string,
) {
  query.setQueryData<Book[]>(booksKey, (books) =>
    books?.flatMap((book) =>
      book.id !== bookId
        ? [book]
        : state === "deleted"
          ? []
          : [{ ...book, state }],
    ),
  );
  query.setQueryData<Book>(queryKeys.book(bookId), (book) =>
    book ? { ...book, state } : book,
  );
  if (state === "deleted") {
    void query.invalidateQueries({ queryKey: ["platform", "cards"] });
    void query.invalidateQueries({
      queryKey: ["platform", "chapters", bookId],
    });
  }
}
const eventSchema = z.object({
  sequence: z.number().int(),
  book_id: z.string(),
  run_id: z.string().nullable(),
  kind: z.string(),
  payload: z.object({
    state: z.string().optional(),
    book_state: z.string().nullable().optional(),
    revision_pending: z.boolean().optional(),
    stage: z.string().optional(),
    run_kind: z.string().optional(),
    revision: z.number().optional(),
    pending_count: z.number().optional(),
    navigation_pending_count: z.number().optional(),
  }),
});

export function applyBookEvent(query: QueryClient, raw: string): number | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  const parsed = eventSchema.safeParse(value);
  if (!parsed.success) return null;
  const event = parsed.data,
    books = query.getQueryData<Book[]>(booksKey);
  if (
    ["book.deleting", "book.deleted", "book.delete_failed"].includes(event.kind)
  ) {
    patchBookDeletion(query, event.book_id, event.payload.state ?? "deleting");
    return event.sequence;
  }
  if (
    books?.some(
      (book) =>
        book.id === event.book_id &&
        ["deleting", "delete_failed"].includes(book.state),
    )
  )
    return event.sequence;
  if (event.kind === "run.progress") {
    void query.invalidateQueries({
      queryKey: queryKeys.runs(event.book_id),
    });
    return event.sequence;
  }
  if (event.kind === "run.created" || event.kind === "run.changed")
    void query.invalidateQueries({
      queryKey: queryKeys.runs(event.book_id),
    });
  if (event.payload.run_kind === "cards") {
    void query.invalidateQueries({
      queryKey: ["platform", "cards", event.book_id],
    });
    void query.invalidateQueries({ queryKey: ["platform", "cards", "all"] });
  }
  if (event.run_id) {
    if (event.kind === "run.changed")
      void query.invalidateQueries({
        queryKey: ["platform", "conversion-page", event.run_id],
      });
    if (event.kind === "run.changed") {
      void query.invalidateQueries({
        queryKey: queryKeys.navigation(event.run_id),
      });
      void query.invalidateQueries({
        queryKey: queryKeys.recovery(event.run_id),
      });
    }
    void query.invalidateQueries({
      queryKey: queryKeys.run(event.run_id),
    });
    if (event.kind === "execution.changed" || event.kind === "run.changed")
      void query.invalidateQueries({
        queryKey: ["platform", "executions", event.run_id],
      });
    if (event.payload.pending_count !== undefined)
      void query.invalidateQueries({
        queryKey: queryKeys.issues(event.run_id),
      });
  }
  if (event.kind === "book.published" || event.kind === "library.revised") {
    void query.invalidateQueries({
      queryKey: ["platform", "chapters", event.book_id],
    });
    void query.invalidateQueries({ queryKey: ["platform", "chapter"] });
    void query.invalidateQueries({ queryKey: ["platform", "search"] });
  }
  if (
    event.payload.run_kind === "cards" ||
    event.kind === "execution.changed" ||
    event.kind === "run.created"
  )
    return event.sequence;
  if (event.kind === "run.changed") {
    const patch: Partial<Book> = {
      ...(event.payload.pending_count !== undefined
        ? { pending_count: event.payload.pending_count }
        : {}),
      ...(event.payload.navigation_pending_count !== undefined
        ? { navigation_pending_count: event.payload.navigation_pending_count }
        : {}),
      ...(event.payload.revision_pending !== undefined
        ? { revision_pending: event.payload.revision_pending }
        : {}),
      ...(event.payload.state
        ? { state: event.payload.book_state ?? event.payload.state }
        : {}),
      ...(event.payload.stage ? { stage: event.payload.stage } : {}),
      ...(event.payload.revision !== undefined
        ? { revision: event.payload.revision }
        : {}),
      run_id: event.run_id,
    };
    updateBooks(
      query,
      (book) => book.id === event.book_id,
      (book) => {
        if (
          book.run_id === event.run_id &&
          book.revision !== undefined &&
          event.payload.revision !== undefined &&
          event.payload.revision <= book.revision
        )
          return book;
        return { ...book, ...patch };
      },
    );
  } else
    void query.invalidateQueries({ queryKey: queryKeys.book(event.book_id) });
  if (
    event.kind !== "run.changed" ||
    !books?.some((book) => book.id === event.book_id)
  ) {
    void query.invalidateQueries({ queryKey: booksKey });
  }
  return event.sequence;
}
