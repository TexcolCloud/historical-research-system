import { queryOptions, replaceEqualDeep } from "@tanstack/react-query";
import { client } from "@/client/api";
import { queryKeys } from "@/client/query-keys";
import type { components } from "@/client/schema";
type NavigationReport = components["schemas"]["NavigationReport"];

export function navigationOptions(runId: string) {
  return queryOptions<NavigationReport>({
    queryKey: queryKeys.navigation(runId),
    structuralSharing: (previous, incoming) => {
      const current = previous as NavigationReport | undefined;
      const next = incoming as NavigationReport;
      return current && current.version > next.version
        ? current
        : replaceEqualDeep(previous, incoming);
    },
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/runs/{run_id}/navigation", {
        params: { path: { run_id: runId } },
        signal,
      });
      if (!data) throw new Error("目录尚未准备好，请稍后重试。");
      return data;
    },
  });
}

export function readingNavigationOptions(bookId: string) {
  return queryOptions<NavigationReport>({
    queryKey: queryKeys.readingNavigation(bookId),
    queryFn: async ({ signal }) => {
      const { data } = await client.GET(
        "/api/v2/books/{book_id}/reading-navigation",
        { params: { path: { book_id: bookId } }, signal },
      );
      if (!data) throw new Error("原书目录暂时无法读取，请重试。");
      return data;
    },
  });
}
