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
    [zoom, setZoom] = useState(100);
  const [begin, setBegin] = useState<number[] | null>(null);
  const [selected, setSelected] = useState({ id: issue.id, index: 0 });
  const index =
    selected.id === issue.id
      ? Math.min(selected.index, Math.max(0, issue.boxes.length - 1))
      : 0;
  const box = issue.boxes[index];
  const context = box
    ? [
        Math.max(0, box[0] - Math.max(0.02, (box[2] - box[0]) * 0.12)),
        Math.max(0, box[1] - Math.max(0.012, (box[3] - box[1]) * 0.5)),
        Math.min(1, box[2] + Math.max(0.02, (box[2] - box[0]) * 0.12)),
        Math.min(1, box[3] + Math.max(0.012, (box[3] - box[1]) * 0.5)),
      ]
    : null;
  function selectRegion(id: string, next: number) {
    setSelected({ id, index: next });
    onSelect?.(id);
  }
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
      {box && context && (
        <>
          {issue.boxes.length > 1 && (
            <nav
              className="review-source-positions"
              aria-label="本疑点的原图位置"
            >
              <button
                type="button"
                aria-label="上一处原图位置"
                disabled={index === 0}
                onClick={() => selectRegion(issue.id, index - 1)}
              >
                上一处
              </button>
              <span>
                位置 {index + 1} / {issue.boxes.length}
              </span>
              <button
                type="button"
                aria-label="下一处原图位置"
                disabled={index === issue.boxes.length - 1}
                onClick={() => selectRegion(issue.id, index + 1)}
              >
                下一处
              </button>
            </nav>
          )}
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
              data-review-id={issue.id}
              data-box-index={index}
              viewBox={`${context[0]} ${context[1]} ${context[2] - context[0]} ${context[3] - context[1]}`}
              preserveAspectRatio="none"
              style={{
                width: `${zoom}%`,
                aspectRatio:
                  ((context[2] - context[0]) * ratio) /
                  (context[3] - context[1]),
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
              <rect
                className="review-crop-region"
                aria-label="当前疑点区域"
                data-review-id={issue.id}
                data-box-index={index}
                x={box[0]}
                y={box[1]}
                width={box[2] - box[0]}
                height={box[3] - box[1]}
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
            if ((e.target as Element).closest("rect[data-review-id]")) return;
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
            {locations.flatMap((item, locationIndex) =>
              item.page === issue.page
                ? item.boxes.map((b, i) => (
                    <rect
                      key={item.id + ":" + i}
                      role="button"
                      tabIndex={0}
                      aria-label={`原图疑点 ${locationIndex + 1}，位置 ${i + 1}`}
                      data-review-id={item.id}
                      data-box-index={i}
                      data-active={item.id === issue.id && i === index}
                      onClick={(e) => {
                        e.stopPropagation();
                        selectRegion(item.id, i);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          selectRegion(item.id, i);
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
