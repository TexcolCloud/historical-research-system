import {
  createContext,
  useCallback,
  useContext,
  useSyncExternalStore,
} from "react";
import { useQueryClient } from "@tanstack/react-query";

export type RuntimeScope = "content" | "workflow_only" | "unknown";

export const RuntimeScopeContext = createContext<RuntimeScope>("unknown");

export function useRuntimeScope() {
  return useContext(RuntimeScopeContext);
}

/** Read the host-confirmed workspace without replacing its query or making another request. */
export function useWorkspaceId() {
  const client = useQueryClient();
  const cache = client.getQueryCache();
  const subscribe = useCallback(
    (listener: () => void) => cache.subscribe(listener),
    [cache],
  );
  const snapshot = useCallback(() => {
    const runtime = client.getQueryData<{ workspace_id?: unknown }>([
      "platform",
      "runtime",
    ]);
    return typeof runtime?.workspace_id === "string"
      ? runtime.workspace_id
      : "";
  }, [client]);
  return useSyncExternalStore(subscribe, snapshot, () => "");
}
