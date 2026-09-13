import { useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "../components/ui/button";
import { client } from "./client";
export default function StartCardsButton({ bookId }: { bookId: string }) {
  const identity = useRef(crypto.randomUUID()),
    query = useQueryClient();
  const start = useMutation({
    mutationFn: async () => {
      const { data, response } = await client.POST(
        "/api/v2/books/{book_id}/card-runs",
        {
          params: { path: { book_id: bookId } },
          body: { request_id: identity.current },
        },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "本书尚未完成核对与入库。"
            : "暂时无法确认制卡请求，请重试；重复点击不会重复创建任务。",
        );
      return data;
    },
    onSuccess: (data) => {
      query.setQueryData(["platform", "run", data.id], data);
      void query.invalidateQueries({ queryKey: ["platform", "runs", bookId] });
    },
  });
  return (
    <div className="platform-review-actions">
      <Button
        variant="outline"
        disabled={start.isPending || start.isSuccess}
        onClick={() => start.mutate()}
      >
        {start.isPending
          ? "正在创建…"
          : start.isSuccess
            ? "任务已创建"
            : "新建制卡任务"}
      </Button>
      {start.isSuccess && (
        <a href={`#/books/${bookId}?tab=execution`}>查看运行进展</a>
      )}
      {start.isError && <p role="alert">{start.error.message}</p>}
    </div>
  );
}
