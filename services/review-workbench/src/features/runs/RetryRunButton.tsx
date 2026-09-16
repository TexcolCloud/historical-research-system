import { useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button.tsx";
import { client } from "@/client/api.ts";
import { booksKey } from "@/hooks/events.ts";
import type { components } from "@/client/schema.d.ts";
export default function RetryRunButton({ runId }: { runId: string }) {
  const identity = useRef(crypto.randomUUID()),
    query = useQueryClient();
  const retry = useMutation({
    mutationFn: async () => {
      const { data, response } = await client.POST(
        "/api/v2/runs/{run_id}/retry",
        {
          params: { path: { run_id: runId } },
          body: { request_id: identity.current },
        },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "任务已经在处理或已完成，请刷新任务状态。"
            : "暂时无法确认重试请求，请再次尝试。",
        );
      return data;
    },
    onSuccess: (data) => {
      query.setQueryData(["platform", "run", runId], data);
      query.setQueryData<components["schemas"]["RunSummary"][]>(
        ["platform", "runs", data.book_id],
        (runs) => runs?.map((run) => (run.id === data.id ? data : run)),
      );
      void query.invalidateQueries({ queryKey: booksKey });
    },
  });
  return (
    <>
      <Button
        variant="outline"
        disabled={retry.isPending || retry.isSuccess}
        onClick={() => retry.mutate()}
      >
        {retry.isPending
          ? "正在提交…"
          : retry.isSuccess
            ? "已加入处理队列"
            : "重试本阶段"}
      </Button>
      {retry.isError && <p role="alert">{retry.error.message}</p>}
    </>
  );
}
