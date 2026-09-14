import type { QueryClient } from "@tanstack/react-query";
import { z } from "zod";
import type { Book } from "./client.ts";

export const booksKey = ["platform", "books"] as const;
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
  query.setQueryData<Book>(["platform", "book", bookId], (book) =>
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
    stage: z.string().optional(),
    run_kind: z.string().optional(),
    revision: z.number().optional(),
    pending_count: z.number().optional(),
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
      queryKey: ["platform", "runs", event.book_id],
    });
    return event.sequence;
  }
  const currentBook =
    books?.find(
      (book) => book.id === event.book_id && book.run_id === event.run_id,
    ) ?? query.getQueryData<Book>(["platform", "book", event.book_id]);
  if (
    event.kind === "run.changed" &&
    currentBook?.run_id === event.run_id &&
    event.payload.revision !== undefined &&
    currentBook?.revision !== undefined &&
    event.payload.revision <= currentBook.revision
  )
    return event.sequence;
  if (event.kind === "run.created" || event.kind === "run.changed")
    void query.invalidateQueries({
      queryKey: ["platform", "runs", event.book_id],
    });
  if (event.payload.run_kind === "cards") {
    void query.invalidateQueries({
      queryKey: ["platform", "cards", event.book_id],
    });
    void query.invalidateQueries({ queryKey: ["platform", "cards", "all"] });
  }
  if (event.run_id) {
    void query.invalidateQueries({
      queryKey: ["platform", "run", event.run_id],
    });
    if (event.kind === "execution.changed" || event.kind === "run.changed")
      void query.invalidateQueries({
        queryKey: ["platform", "executions", event.run_id],
      });
    if (event.payload.pending_count !== undefined)
      void query.invalidateQueries({
        queryKey: ["platform", "issues", event.run_id],
      });
  }
  if (event.kind === "book.published")
    void query.invalidateQueries({
      queryKey: ["platform", "chapters", event.book_id],
    });
  if (
    event.payload.run_kind === "cards" ||
    event.kind === "execution.changed" ||
    event.kind === "run.created"
  )
    return event.sequence;
  if (event.kind === "run.changed") {
    query.setQueryData<Book>(["platform", "book", event.book_id], (current) =>
      current
        ? {
            ...current,
            ...(event.payload.state
              ? { state: event.payload.book_state ?? event.payload.state }
              : {}),
            ...(event.payload.stage ? { stage: event.payload.stage } : {}),
            ...(event.payload.revision !== undefined
              ? { revision: event.payload.revision }
              : {}),
            run_id: event.run_id,
          }
        : current,
    );
  } else
    void query.invalidateQueries({
      queryKey: ["platform", "book", event.book_id],
    });
  if (
    event.kind !== "run.changed" ||
    !books?.some((book) => book.id === event.book_id)
  ) {
    void query.invalidateQueries({ queryKey: booksKey });
  } else {
    query.setQueryData<Book[]>(booksKey, (current) =>
      current?.map((book) =>
        book.id === event.book_id
          ? {
              ...book,
              ...(event.payload.state
                ? { state: event.payload.book_state ?? event.payload.state }
                : {}),
              ...(event.payload.stage ? { stage: event.payload.stage } : {}),
              ...(event.payload.revision !== undefined
                ? { revision: event.payload.revision }
                : {}),
              run_id: event.run_id,
            }
          : book,
      ),
    );
  }
  return event.sequence;
}
