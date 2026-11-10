import { useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/client/query-keys";
import { client } from "@/client/api";
import { updateBooks } from "@/features/books/book-cache";
import { useRuntimeScope } from "@/app/runtime-scope";
import type { components } from "@/client/schema";
import type { NavigationReport } from "./navigation-query";
import type { NavigationChoice } from "./NavigationReview";

export function useNavigationActions({
  runId,
  bookId,
  onDecision,
  onRecommendation,
}: {
  runId: string;
  bookId: string;
  onDecision: (
    report: NavigationReport,
    choice: NavigationChoice | null,
    id: string,
  ) => void;
  onRecommendation: (report: NavigationReport) => void;
}) {
  const query = useQueryClient();
  const runtimeScope = useRuntimeScope();
  const pendingDecision = useRef<{ signature: string; id: string } | null>(
    null,
  );
  function commitReport(data: components["schemas"]["NavigationReport"]) {
    const committed =
      query.setQueryData<components["schemas"]["NavigationReport"]>(
        queryKeys.navigation(runId),
        data,
      ) ?? data;
    updateBooks(
      query,
      (book) => book.id === bookId,
      (book) => ({
        ...book,
        navigation_pending_count: committed.pending_count,
        navigation_missing_page_count: new Set([
          ...(committed.missing_pages ?? []),
          ...(committed.unresolved_pages ?? []),
        ]).size,
      }),
    );
    void query.invalidateQueries({
      queryKey: queryKeys.readingNavigation(bookId),
    });
    return committed;
  }
  const decision = useMutation({
    mutationFn: async ({
      choice,
      sha,
      id,
    }: {
      choice: NavigationChoice | null;
      sha: string;
      id: string;
    }) => {
      if (runtimeScope === "unknown")
        throw new Error("工作区范围未确认，请重新确认后提交目录决定。");
      await query.cancelQueries({ queryKey: queryKeys.navigation(runId) });
      const signature = JSON.stringify([id, sha, choice]);
      if (pendingDecision.current?.signature !== signature)
        pendingDecision.current = { signature, id: crypto.randomUUID() };
      const body = {
        decision_id: pendingDecision.current.id,
        expected_sha256: sha,
        target_page: choice?.page ?? null,
        target_anchor: choice?.anchor ?? "",
        ...(choice?.title_spans?.length
          ? { target_spans: choice.title_spans, target_title: choice.title }
          : {}),
        decision_scope:
          runtimeScope === "workflow_only"
            ? ("workflow_test" as const)
            : ("content" as const),
      };
      const { data, response } = await client.POST(
        "/api/v2/runs/{run_id}/navigation/{entry_id}/decision",
        {
          params: { path: { run_id: runId, entry_id: id } },
          body,
        },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "目录或正文已更新。你的选择尚未提交，请重新载入证据后核对。"
            : "未能保存跳转决定，请重试；本次选择仍保留。",
        );
      return data;
    },
    onSuccess: async (data, { choice, id }) => {
      await query.cancelQueries({ queryKey: queryKeys.navigation(runId) });
      const committed = commitReport(data);
      pendingDecision.current = null;
      onDecision(committed, choice, id);
      // The next bridge event replaces these committed local counters after projection.
    },
  });
  const recommendation = useMutation({
    mutationFn: async () => {
      if (runtimeScope === "unknown")
        throw new Error("工作区范围未确认，暂不能更新目录推荐。");
      await query.cancelQueries({ queryKey: queryKeys.navigation(runId) });
      const { data, response } = await client.POST(
        "/api/v2/runs/{run_id}/navigation/refresh",
        {
          params: { path: { run_id: runId } },
        },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "当前任务暂不能更新推荐，请在处理进展中核对任务状态后重试。"
            : "推荐更新未完成，已有目录与选择均已保留，请重试。",
        );
      return data;
    },
    onSuccess: async (data) => {
      await query.cancelQueries({ queryKey: queryKeys.navigation(runId) });
      const committed = commitReport(data);
      onRecommendation(committed);
    },
  });
  return {
    decision,
    recommendation,
    busy: decision.isPending || recommendation.isPending,
    approvalReady: runtimeScope !== "unknown",
  };
}
