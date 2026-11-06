import { createContext, useContext } from "react";

export type RuntimeScope = "content" | "workflow_only" | "unknown";

export const RuntimeScopeContext = createContext<RuntimeScope>("unknown");

export function useRuntimeScope() {
  return useContext(RuntimeScopeContext);
}
