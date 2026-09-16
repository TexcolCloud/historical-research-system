import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { applyBookEvent } from "@/hooks/events.ts";

/** Own the one event stream and fallback refresh for every workspace route. */
export function useWorkspaceEvents() {
  const query = useQueryClient();
  const [connected, setConnected] = useState(true);
  useEffect(() => {
    const events = new EventSource("/api/v2/events");
    events.onopen = () => setConnected(true);
    events.onerror = () => setConnected(false);
    let cursor = 0;
    events.onmessage = (event) => {
      if (Number(event.lastEventId) <= cursor) return;
      cursor = applyBookEvent(query, event.data) ?? cursor;
    };
    return () => events.close();
  }, [query]);
  useEffect(() => {
    const timer = window.setInterval(
      () =>
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
        }),
      30000,
    );
    return () => window.clearInterval(timer);
  }, [connected, query]);
  return connected;
}
