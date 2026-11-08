import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { readOriginalPage } from "@/client/api";
import { Button } from "@/components/ui/button";

export default function OriginalPage({
  runId,
  page,
}: {
  runId: string;
  page: number;
}) {
  const [zoom, setZoom] = useState(100);
  const original = useQuery({
    queryKey: ["platform", "original-page", runId, page],
    staleTime: Infinity,
    queryFn: ({ signal }) => readOriginalPage(runId, page, signal),
  });
  return (
    <figure className="research-original-page">
      <div className="research-original-toolbar">
        <a
          href={`/api/v2/runs/${encodeURIComponent(runId)}/artifacts/original.pdf#page=${page}`}
          target="_blank"
          rel="noreferrer"
        >
          打开原 PDF
        </a>
        <label>
          缩放{" "}
          <input
            type="range"
            aria-label="原图放大倍数"
            min={75}
            max={300}
            step={25}
            value={zoom}
            onChange={(event) => setZoom(Number(event.target.value))}
          />
          <output>{zoom}%</output>
        </label>
      </div>
      {original.isPending ? (
        <p role="status">正在读取原书第 {page} 页…</p>
      ) : original.isError ? (
        <p role="alert">
          {original.error.message}
          <Button onClick={() => void original.refetch()}>重新读取原图</Button>
        </p>
      ) : (
        <div className="research-original-viewport">
          <a href={original.data} target="_blank" rel="noreferrer">
            <img
              src={original.data}
              alt={`原书第 ${page} 页`}
              style={{ width: `${zoom}%`, maxWidth: "none" }}
            />
          </a>
        </div>
      )}
    </figure>
  );
}
