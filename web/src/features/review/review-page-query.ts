import { queryOptions } from "@tanstack/react-query";
import { client } from "@/client/api.ts";

export const reviewPageOptions = (runId: string, page: number) =>
  queryOptions({
    queryKey: ["platform", "conversion-page", runId, page],
    queryFn: async ({ signal }) => {
      const { data, response } = await client.GET(
        "/api/v2/runs/{run_id}/review-pages/{page}",
        { signal, params: { path: { run_id: runId, page } } },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "识别底稿尚未保存，请等待 OCR 完成。"
            : "暂时无法读取底稿，请重试。",
        );
      return data;
    },
  });
