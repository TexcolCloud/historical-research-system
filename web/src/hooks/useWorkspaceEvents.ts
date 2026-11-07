import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { applyBookEvent, booksKey } from "@/hooks/events.ts";
import { client } from "@/client/api.ts";

/** One snapshot/cursor and one resumable stream for the workspace lifetime. */
export function useWorkspaceEvents() {
  const query = useQueryClient();
  const [connected, setConnected] = useState(true);
  useEffect(() => {
    const abort = new AbortController();
    let events: EventSource | undefined,
      retry: number | undefined,
      flush: number | undefined;
    let cursor = 0,
      failures = 0;
    const pending = new Map<string, string>();
    const deliver = () => {
      flush = undefined;
      for (const raw of pending.values()) applyBookEvent(query, raw);
      pending.clear();
    };
    const open = async () => {
      const started = Date.now();
      try {
        const { data, error } = await client.GET("/api/v2/events/snapshot", {
          signal: abort.signal,
        });
        if (
          error ||
          !data ||
          !Array.isArray(data.books) ||
          !Number.isSafeInteger(data.cursor)
        )
          throw new Error("工作区快照暂不可用");
        if (abort.signal.aborted) return;
        cursor = data.cursor;
        if ((query.getQueryState(booksKey)?.dataUpdatedAt ?? 0) <= started)
          query.setQueryData(booksKey, data.books);
        else void query.invalidateQueries({ queryKey: booksKey });
        events = new EventSource(`/api/v2/events?after=${cursor}`);
        events.onopen = () => {
          failures = 0;
          setConnected(true);
        };
        events.onerror = () => setConnected(false);
        events.onmessage = (event) => {
          const next = Number(event.lastEventId);
          if (!Number.isSafeInteger(next) || next <= cursor) return;
          try {
            const value = JSON.parse(event.data);
            cursor = next;
            const key = `${value.kind}:${value.book_id}:${value.run_id}`;
            const previous = pending.get(key);
            if (
              value.kind === "research.changed" &&
              previous &&
              JSON.parse(previous).payload?.artifacts_changed === true
            ) {
              value.payload = { ...value.payload, artifacts_changed: true };
            }
            pending.set(
              key,
              value.kind === "research.changed"
                ? JSON.stringify(value)
                : event.data,
            );
            flush ??= window.setTimeout(deliver, 200);
          } catch {
            /* A malformed event cannot replace cached state. */
          }
        };
      } catch {
        if (abort.signal.aborted) return;
        setConnected(false);
        retry = window.setTimeout(
          () => void open(),
          Math.min(30000, 1000 * 2 ** failures++),
        );
      }
    };
    void open();
    return () => {
      abort.abort();
      events?.close();
      window.clearTimeout(retry);
      window.clearTimeout(flush);
    };
  }, [query]);
  useEffect(() => {
    if (connected) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      void query.invalidateQueries({
        predicate: (entry) =>
          entry.queryKey[0] === "platform" &&
          [
            "books",
            "run",
            "book",
            "runs",
            "issues",
            "executions",
            "cards",
            "chapters",
          ].includes(String(entry.queryKey[1])),
        refetchType: "active",
      });
    }, 30000);
    return () => window.clearInterval(timer);
  }, [connected, query]);
  return connected;
}
