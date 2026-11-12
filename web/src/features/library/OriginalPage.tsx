import { useLayoutEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { readOriginalPage } from "@/client/api";
import { Button } from "@/components/ui/button";

type OriginalViewProps = { runId: string; page: number; viewId?: string };
type OriginalView = { zoom: number; top: number; left: number };

function OriginalPageSession({
  runId,
  page,
  viewId = "page",
}: OriginalViewProps) {
  const cache = useQueryClient();
  const key = ["platform", "original-view", runId, page, viewId];
  const [initial] = useState(() => cache.getQueryData<OriginalView>(key));
  const [zoom, setZoom] = useState(initial?.zoom ?? 100);
  const viewport = useRef<HTMLDivElement>(null);
  const view = useRef<OriginalView>(initial ?? { zoom: 100, top: 0, left: 0 });
  view.current.zoom = zoom;
  useLayoutEffect(() => {
    cache.setQueryDefaults(key, { gcTime: Infinity });
    return () => {
      cache.setQueryData(key, { ...view.current });
    };
  }, [cache, runId, page, viewId]);
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
        <p role="status">正在读取PDF 第 {page} 页…</p>
      ) : original.isError ? (
        <p role="alert">
          {original.error.message}
          <Button onClick={() => void original.refetch()}>重新读取原图</Button>
        </p>
      ) : (
        <div
          className="research-original-viewport"
          ref={viewport}
          onScroll={(event) => {
            view.current = {
              zoom,
              top: event.currentTarget.scrollTop,
              left: event.currentTarget.scrollLeft,
            };
          }}
        >
          <a href={original.data} target="_blank" rel="noreferrer">
            <img
              src={original.data}
              alt={`PDF 第 ${page} 页`}
              style={{ width: `${zoom}%`, maxWidth: "none" }}
              onLoad={() => {
                if (viewport.current && initial) {
                  viewport.current.scrollTop = initial.top;
                  viewport.current.scrollLeft = initial.left;
                }
              }}
            />
          </a>
        </div>
      )}
    </figure>
  );
}

export default function OriginalPage(props: OriginalViewProps) {
  return (
    <OriginalPageSession
      key={`${props.runId}:${props.page}:${props.viewId ?? "page"}`}
      {...props}
    />
  );
}
