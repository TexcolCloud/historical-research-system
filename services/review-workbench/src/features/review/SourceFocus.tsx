import { useState } from "react";
import type { ReviewLocation } from "@/editor/review-highlights.ts";

export default function SourceFocus({
  src,
  issue,
  onBox,
  locations = [issue],
  onSelect,
}: {
  src: string;
  issue: ReviewLocation;
  onBox: (box: number[]) => void;
  locations?: ReviewLocation[];
  onSelect?: (id: string) => void;
}) {
  const [ratio, setRatio] = useState(1),
    [zoom, setZoom] = useState(150);
  const [begin, setBegin] = useState<number[] | null>(null);
  const box = issue.boxes[0];
  return (
    <figure className="review-source-focus">
      <figcaption>
        疑点原图 · 第 {issue.page} 页 ·{" "}
        {box
          ? issue.image_precision === "line"
            ? "定位到行，请结合上下文核对"
            : "仅定位到区域，请结合上下文核对"
          : "需查看整页，可拖框放大疑点区域"}
      </figcaption>
      {box && (
        <>
          <label>
            局部放大{" "}
            <input
              aria-label="原图局部放大倍数"
              type="range"
              min="100"
              max="300"
              step="25"
              value={zoom}
              onChange={(e) => setZoom(Number(e.target.value))}
            />
            {zoom}%
          </label>
          <div className="review-crop-scroll">
            <svg
              aria-label="疑点局部原图"
              viewBox={`${box[0]} ${box[1]} ${box[2] - box[0]} ${box[3] - box[1]}`}
              preserveAspectRatio="none"
              style={{
                width: `${zoom}%`,
                aspectRatio: ((box[2] - box[0]) * ratio) / (box[3] - box[1]),
              }}
            >
              <image
                href={src}
                x="0"
                y="0"
                width="1"
                height="1"
                preserveAspectRatio="none"
              />
            </svg>
          </div>
        </>
      )}
      <details className="review-full-original" open={!box}>
        <summary>查看整页与调整查看区域</summary>
        <div
          className="review-source-sheet"
          onPointerDown={(e) => {
            if (e.button !== 0) return;
            const r = e.currentTarget.getBoundingClientRect();
            setBegin([
              (e.clientX - r.left) / r.width,
              (e.clientY - r.top) / r.height,
            ]);
            e.currentTarget.setPointerCapture(e.pointerId);
          }}
          onPointerUp={(e) => {
            if (!begin) return;
            const r = e.currentTarget.getBoundingClientRect();
            const end = [
              Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
              Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
            ];
            const b = [
              Math.min(begin[0], end[0]),
              Math.min(begin[1], end[1]),
              Math.max(begin[0], end[0]),
              Math.max(begin[1], end[1]),
            ];
            setBegin(null);
            if (b[2] - b[0] > 0.005 && b[3] - b[1] > 0.005) onBox(b);
          }}
        >
          <img
            src={src}
            draggable={false}
            alt={`原书第 ${issue.page} 页`}
            onLoad={(e) =>
              setRatio(
                e.currentTarget.naturalWidth / e.currentTarget.naturalHeight,
              )
            }
          />
          <svg
            viewBox="0 0 1 1"
            preserveAspectRatio="none"
            aria-label="原图疑点框"
          >
            {locations.flatMap((item, index) =>
              item.page === issue.page
                ? item.boxes.map((b, i) => (
                    <rect
                      key={item.id + ":" + i}
                      role="button"
                      tabIndex={0}
                      aria-label={`原图疑点 ${index + 1}`}
                      data-active={item.id === issue.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelect?.(item.id);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onSelect?.(item.id);
                        }
                      }}
                      x={b[0]}
                      y={b[1]}
                      width={b[2] - b[0]}
                      height={b[3] - b[1]}
                    />
                  ))
                : [],
            )}
          </svg>
        </div>
        <p className="platform-inline-status">
          在原图上按住并拖动可框选放大区域，只调整本次查看范围。
        </p>
      </details>
      <a href={src} target="_blank" rel="noreferrer">
        打开完整原图 · 第 {issue.page} 页
      </a>
    </figure>
  );
}
