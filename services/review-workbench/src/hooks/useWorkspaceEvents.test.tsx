// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useWorkspaceEvents } from "./useWorkspaceEvents";
import { respondWith } from "@/test/responses";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
test("starts at the snapshot cursor, coalesces bursts, and polls only while disconnected", async () => {
  const streams: {
    url: string;
    onopen?: () => void;
    onerror?: () => void;
    onmessage?: (e: unknown) => void;
  }[] = [];
  vi.stubGlobal(
    "EventSource",
    class {
      constructor(public url: string) {
        streams.push(this);
      }
      close() {}
    },
  );
  respondWith(() => ({ books: [], cursor: 330735 }));
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Test() {
    useWorkspaceEvents();
    return null;
  }
  render(
    <QueryClientProvider client={query}>
      <Test />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(streams).toHaveLength(1));
  expect(streams[0].url).toBe("/api/v2/events?after=330735");
  vi.useFakeTimers();
  const invalidate = vi.spyOn(query, "invalidateQueries");
  act(() => streams[0].onopen?.());
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(invalidate).not.toHaveBeenCalled();
  act(() => {
    for (let i = 1; i <= 100; i++)
      streams[0].onmessage?.({
        lastEventId: String(330735 + i),
        data: JSON.stringify({
          sequence: 330735 + i,
          book_id: "b",
          run_id: "r",
          kind: "execution.changed",
          payload: {},
        }),
      });
  });
  expect(invalidate).not.toHaveBeenCalled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200);
  });
  expect(invalidate).toHaveBeenCalledTimes(2);
  invalidate.mockClear();
  act(() => streams[0].onerror?.());
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30000);
  });
  expect(invalidate).toHaveBeenCalledTimes(1);
});
