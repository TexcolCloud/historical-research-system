// @vitest-environment happy-dom
import { expect, test } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { client } from "@/client/api.ts";
import { queryKeys } from "@/client/query-keys.ts";
import { navigationOptions } from "./navigation-query.ts";

test("an older in-flight navigation report cannot undo a committed decision", async () => {
  const oldReport = {
    state: "available",
    version: 1,
    sha256: "a".repeat(64),
    entries: [],
    pending_count: 0,
    coverage_complete: true,
  };
  const committed = { ...oldReport, version: 2, sha256: "b".repeat(64) };
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => {
    release = resolve;
  });
  const middleware = {
    onRequest: async () => {
      await delayed;
      return Response.json(oldReport);
    },
  };
  client.use(middleware);
  const query = new QueryClient();
  try {
    const pending = query.fetchQuery(navigationOptions("run"));
    query.setQueryData(queryKeys.navigation("run"), committed);
    release();
    await pending;
    expect(query.getQueryData(queryKeys.navigation("run"))).toEqual(committed);
  } finally {
    client.eject(middleware);
    query.clear();
  }
});
