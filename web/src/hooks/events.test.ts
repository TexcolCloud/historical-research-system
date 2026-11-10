import { expect, test, vi } from "vitest";
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { applyBookEvent, booksKey } from "@/hooks/events.ts";
import { queryKeys } from "@/client/query-keys";

test("an explicit indexed version refreshes source and research search while completed heartbeats do not", async () => {
  const query = new QueryClient();
  const book = {
    id: "book",
    run_id: "source",
    state: "ready",
    revision: 1,
    revision_pending: false,
  };
  query.setQueryData(booksKey, [book]);
  query.setQueryData(queryKeys.book("book"), book);
  query.setQueryData(queryKeys.readingNavigation("book"), []);
  const researchSearch = ["research", "search", "新四军", ""];
  const sourceSearch = ["platform", "search", "book", { q: "新四军" }];
  query.setQueryData(researchSearch, []);
  query.setQueryData(sourceSearch, []);
  const rerank = vi.fn(async () => []);
  const observer = new QueryObserver(query, {
    queryKey: researchSearch,
    queryFn: rerank,
    staleTime: Infinity,
  });
  const unsubscribe = observer.subscribe(() => undefined);
  try {
    applyBookEvent(
      query,
      JSON.stringify({
        sequence: 2,
        book_id: "book",
        run_id: "source",
        kind: "run.changed",
        payload: {
          state: "completed",
          stage: "complete",
          revision: 2,
          revision_pending: false,
        },
      }),
    );
    expect(rerank).not.toHaveBeenCalled();
    expect(query.getQueryState(sourceSearch)?.isInvalidated).toBe(false);
    const beforeIndex = query.getQueryData(queryKeys.book("book"));
    applyBookEvent(
      query,
      JSON.stringify({
        sequence: 3,
        book_id: "book",
        run_id: "source",
        kind: "library.indexed",
        payload: {
          publication_sha256: "published",
          chapters_sha256: "chapters",
          retrieval_generation: "new-index",
        },
      }),
    );
    await vi.waitFor(() => expect(rerank).toHaveBeenCalledTimes(1));
    expect(query.getQueryState(sourceSearch)?.isInvalidated).toBe(true);
    expect(
      query.getQueryState(queryKeys.readingNavigation("book"))?.isInvalidated,
    ).toBe(false);
    expect(query.getQueryState(queryKeys.book("book"))?.isInvalidated).toBe(
      false,
    );
    expect(query.getQueryData(queryKeys.book("book"))).toEqual(beforeIndex);
  } finally {
    unsubscribe();
    query.clear();
  }
});

test("research metadata events leave source and content caches alone, while changed artifacts refresh visible search", async () => {
  const query = new QueryClient();
  const book = { id: "book", run_id: "source", state: "ready" };
  const searchKey = ["research", "search", "新四军", ""];
  const cardKey = ["research", "card", "card"];
  const researchBook = ["research", "book", "book"];
  const researchRun = ["research", "run", "research-run"];
  const questionKey = ["research", "question", "q"];
  query.setQueryData(questionKey, {
    runs: [{ run_id: "research-run", state: "paused" }],
  });
  const sourceKeys = [
    queryKeys.book("book"),
    queryKeys.run("source"),
    queryKeys.runs("book"),
    queryKeys.readingNavigation("book"),
    booksKey,
  ];
  for (const key of sourceKeys)
    query.setQueryData(key, key === booksKey ? [book] : book);
  for (const key of [searchKey, cardKey, researchBook, researchRun])
    query.setQueryData(key, []);
  const rerank = vi.fn(async () => []);
  const observer = new QueryObserver(query, {
    queryKey: searchKey,
    queryFn: rerank,
    staleTime: Infinity,
  });
  const unsubscribe = observer.subscribe(() => undefined);
  try {
    const event = (artifacts_changed: boolean) =>
      JSON.stringify({
        sequence: artifacts_changed ? 2 : 1,
        book_id: "book",
        run_id: "source",
        kind: "research.changed",
        payload: {
          research_run_id: "research-run",
          state: "completed",
          version: artifacts_changed ? 4 : 3,
          artifacts_changed,
        },
      });
    applyBookEvent(query, event(false));
    expect(query.getQueryState(researchBook)?.isInvalidated).toBe(true);
    expect(query.getQueryState(researchRun)?.isInvalidated).toBe(true);
    expect(query.getQueryState(questionKey)?.isInvalidated).toBe(true);
    expect(query.getQueryState(cardKey)?.isInvalidated).toBe(false);
    expect(rerank).not.toHaveBeenCalled();
    applyBookEvent(query, event(true));
    await vi.waitFor(() => expect(rerank).toHaveBeenCalledTimes(1));
    expect(query.getQueryState(cardKey)?.isInvalidated).toBe(true);
    for (const key of sourceKeys) {
      expect(query.getQueryState(key)?.isInvalidated).toBe(false);
      expect(query.getQueryData(key)).toEqual(key === booksKey ? [book] : book);
    }
  } finally {
    unsubscribe();
    query.clear();
  }
});

test("idle research search does not rerun when the unobserved books snapshot is collected before ordinary run events", async () => {
  const query = new QueryClient({
    defaultOptions: { queries: { gcTime: 10, retry: false } },
  });
  const key = ["research", "search", "新四军", ""];
  query.setQueryData(key, []);
  query.setQueryData(booksKey, [
    { id: "book", run_id: "source", revision: 1, revision_pending: false },
  ]);
  const rerank = vi.fn(async () => []);
  const observer = new QueryObserver(query, {
    queryKey: key,
    queryFn: rerank,
    staleTime: Infinity,
  });
  const unsubscribe = observer.subscribe(() => undefined);
  try {
    await vi.waitFor(() =>
      expect(query.getQueryData(booksKey)).toBeUndefined(),
    );
    for (const revision of [2, 3, 4]) {
      applyBookEvent(
        query,
        JSON.stringify({
          sequence: revision,
          book_id: "book",
          run_id: "source",
          kind: "run.changed",
          payload: {
            state: "completed",
            stage: "complete",
            revision,
            revision_pending: false,
          },
        }),
      );
    }
    expect(rerank).not.toHaveBeenCalled();
  } finally {
    unsubscribe();
    query.clear();
  }
});

test.each(["list", "detail"])(
  "a known %s revision transition refreshes research once, while its repeated status stays cached",
  async (source) => {
    const query = new QueryClient();
    const before = {
      id: "book",
      run_id: "source",
      revision: 1,
      revision_pending: false,
    };
    query.setQueryData(
      source === "list" ? booksKey : queryKeys.book("book"),
      source === "list" ? [before] : before,
    );
    const key = ["research", "search", "新四军", ""];
    query.setQueryData(key, []);
    const rerank = vi.fn(async () => []);
    const observer = new QueryObserver(query, {
      queryKey: key,
      queryFn: rerank,
      staleTime: Infinity,
    });
    const unsubscribe = observer.subscribe(() => undefined);
    try {
      const changed = (revision: number) =>
        JSON.stringify({
          sequence: revision,
          book_id: "book",
          run_id: "source",
          kind: "run.changed",
          payload: { revision, revision_pending: true },
        });
      applyBookEvent(query, changed(2));
      await vi.waitFor(() => expect(rerank).toHaveBeenCalledTimes(1));
      applyBookEvent(query, changed(3));
      expect(rerank).toHaveBeenCalledTimes(1);
    } finally {
      unsubscribe();
      query.clear();
    }
  },
);

test("publication and deletion invalidate research evidence while ordinary progress leaves it cached", () => {
  const query = new QueryClient();
  const key = ["research", "card", "card"];
  const event = (kind: string) =>
    JSON.stringify({
      sequence: 1,
      book_id: "book",
      run_id: "source",
      kind,
      payload: {},
    });
  query.setQueryData(key, { book_id: "book", source_current: true });
  applyBookEvent(query, event("run.progress"));
  expect(query.getQueryState(key)?.isInvalidated).toBe(false);
  applyBookEvent(query, event("book.published"));
  expect(query.getQueryState(key)?.isInvalidated).toBe(true);
  query.setQueryData(key, { book_id: "book", source_current: false });
  applyBookEvent(query, event("book.deleted"));
  expect(query.getQueryState(key)?.isInvalidated).toBe(true);
});

test.each(["run.changed", "book.published", "library.revised"])(
  "%s refreshes the published directory without substituting the live report",
  async (kind) => {
    const query = new QueryClient();
    const publishedKey = queryKeys.readingNavigation("book");
    const otherPublishedKey = queryKeys.readingNavigation("other");
    const original = {
      sha256: "old-published",
      entries: [{ status: "not_found" }],
    };
    const updated = {
      sha256: "new-published",
      entries: [{ status: "unlinked_manual" }],
    };
    const live = {
      sha256: "live-report",
      entries: [{ status: "matched_manual" }],
    };
    query.setQueryData(publishedKey, original);
    query.setQueryData(otherPublishedKey, original);
    query.setQueryData(queryKeys.navigation("run"), live);
    const reload = vi.fn(async () => updated);
    const observer = new QueryObserver(query, {
      queryKey: publishedKey,
      queryFn: reload,
      staleTime: Infinity,
    });
    const unsubscribe = observer.subscribe(() => undefined);
    try {
      expect(reload).not.toHaveBeenCalled();
      applyBookEvent(
        query,
        JSON.stringify({
          sequence: 12,
          book_id: "book",
          run_id: "run",
          kind,
          payload:
            kind === "run.changed" ? { navigation_pending_count: 0 } : {},
        }),
      );
      await vi.waitFor(() =>
        expect(query.getQueryData(publishedKey)).toEqual(updated),
      );
      expect(reload).toHaveBeenCalledTimes(1);
      expect(query.getQueryData(queryKeys.navigation("run"))).toEqual(live);
      expect(query.getQueryState(otherPublishedKey)?.isInvalidated).toBe(false);
    } finally {
      unsubscribe();
      query.clear();
    }
  },
);

test("completed run events preserve the book's published reading state", () => {
  const query = new QueryClient();
  const book = { id: "book", run_id: "run", state: "processing", revision: 1 };
  query.setQueryData(booksKey, [book]);
  query.setQueryData(["platform", "book", "book"], book);
  applyBookEvent(
    query,
    JSON.stringify({
      sequence: 3,
      book_id: "book",
      run_id: "run",
      kind: "run.changed",
      payload: { state: "completed", stage: "complete", revision: 2 },
    }),
  );
  expect(query.getQueryData(["platform", "book", "book"])).toMatchObject({
    state: "ready",
    revision: 2,
  });
  expect(query.getQueryData(booksKey)).toMatchObject([{ state: "ready" }]);
});

test("a newer list does not prevent the detail cache from catching up independently", () => {
  const query = new QueryClient();
  const book = { id: "book", run_id: "run", state: "ready", revision: 9 };
  query.setQueryData(booksKey, [book]);
  query.setQueryData(["platform", "book", "book"], {
    ...book,
    state: "running",
    revision: 3,
  });
  applyBookEvent(
    query,
    JSON.stringify({
      sequence: 8,
      book_id: "book",
      run_id: "run",
      kind: "run.changed",
      payload: { state: "awaiting_review", revision: 5 },
    }),
  );
  expect(query.getQueryData(booksKey)).toEqual([book]);
  expect(query.getQueryData(["platform", "book", "book"])).toMatchObject({
    state: "awaiting_review",
    revision: 5,
  });
});

test("page telemetry refreshes only run progress without invalidating reading or review content", () => {
  const query = new QueryClient();
  for (const key of [
    booksKey,
    ["platform", "runs", "book"],
    ["platform", "issues", "run"],
    ["platform", "chapters", "book"],
    queryKeys.readingNavigation("book"),
  ]) {
    query.setQueryData(key, []);
  }
  applyBookEvent(
    query,
    JSON.stringify({
      sequence: 11,
      book_id: "book",
      run_id: "run",
      kind: "run.progress",
      payload: { progress: { completed: 12, total: 481 } },
    }),
  );
  expect(query.getQueryState(["platform", "runs", "book"])?.isInvalidated).toBe(
    true,
  );
  for (const key of [
    booksKey,
    ["platform", "issues", "run"],
    ["platform", "chapters", "book"],
    queryKeys.readingNavigation("book"),
  ]) {
    expect(query.getQueryState(key)?.isInvalidated).toBe(false);
  }
});

test("progress patches only its book without dropping the cached list", () => {
  const query = new QueryClient();
  const other = { id: "other", title: "另一册", state: "processing" };
  query.setQueryData(booksKey, [
    { id: "book", title: "原书", state: "processing" },
    other,
  ]);
  applyBookEvent(
    query,
    JSON.stringify({
      sequence: 3,
      book_id: "book",
      run_id: "run",
      kind: "run.changed",
      payload: { state: "awaiting_review", stage: "review" },
    }),
  );
  expect(query.getQueryData(booksKey)).toEqual([
    {
      id: "book",
      title: "原书",
      state: "awaiting_review",
      stage: "review",
      run_id: "run",
    },
    other,
  ]);
  expect(applyBookEvent(query, "invalid")).toBeNull();
});

test("card execution updates never replace the parent book ingestion state", () => {
  const query = new QueryClient();
  const book = {
    id: "book",
    run_id: "book-run",
    state: "ready",
    title: "原书",
  };
  query.setQueryData(booksKey, [book]);
  applyBookEvent(
    query,
    JSON.stringify({
      sequence: 8,
      book_id: "book",
      run_id: "card-run",
      kind: "run.changed",
      payload: { state: "failed", stage: "vision", run_kind: "cards" },
    }),
  );
  expect(query.getQueryData(booksKey)).toEqual([book]);
});

test("replayed older progress cannot regress a freshly loaded book", () => {
  const query = new QueryClient();
  const book = {
    id: "book",
    run_id: "run",
    state: "ready",
    revision: 9,
    title: "原书",
  };
  query.setQueryData(booksKey, [book]);
  applyBookEvent(
    query,
    JSON.stringify({
      sequence: 2,
      book_id: "book",
      run_id: "run",
      kind: "run.changed",
      payload: { state: "processing", revision: 3 },
    }),
  );
  expect(query.getQueryData(booksKey)).toEqual([book]);
});

test("card failure refreshes its run and execution graph despite a higher book revision", () => {
  const query = new QueryClient();
  const book = {
    id: "book",
    run_id: "book-run",
    state: "ready",
    revision: 300,
  };
  query.setQueryData(booksKey, [book]);
  query.setQueryData(["platform", "book", "book"], book);
  const keys = [
    ["platform", "runs", "book"],
    ["platform", "executions", "card-run"],
  ];
  keys.forEach((key) => query.setQueryData(key, []));
  applyBookEvent(
    query,
    JSON.stringify({
      sequence: 301,
      book_id: "book",
      run_id: "card-run",
      kind: "run.changed",
      payload: {
        state: "failed",
        stage: "planning",
        run_kind: "cards",
        revision: 2,
      },
    }),
  );
  keys.forEach((key) =>
    expect(query.getQueryState(key)?.isInvalidated).toBe(true),
  );
  expect(query.getQueryData(booksKey)).toEqual([book]);
  expect(query.getQueryData(["platform", "book", "book"])).toEqual(book);
});
