import { useEffect, useRef, useState } from "react";
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
  const [ratio, setRatio] = useState(1);
  const [zoom, setZoom] = useState(100);
  const [crop, setCrop] = useState(false);
  const [begin, setBegin] = useState<number[] | null>(null);
  const [selected, setSelected] = useState({ id: issue.id, index: 0 });
  const viewport = useRef<HTMLDivElement>(null);
  const original = useRef<HTMLDivElement>(null);
  const index =
    selected.id === issue.id
      ? Math.min(selected.index, Math.max(0, issue.boxes.length - 1))
      : 0;
  const box = issue.image_precision !== "page" ? issue.boxes[index] : undefined;
  const context = box
    ? [
        Math.max(0, box[0] - Math.max(0.02, (box[2] - box[0]) * 0.12)),
        Math.max(0, box[1] - Math.max(0.012, (box[3] - box[1]) * 0.5)),
        Math.min(1, box[2] + Math.max(0.02, (box[2] - box[0]) * 0.12)),
        Math.min(1, box[3] + Math.max(0.012, (box[3] - box[1]) * 0.5)),
      ]
    : null;
  const showCrop = crop && Boolean(box);
  function revealSource() {
    const pane = viewport.current;
    const sheet = original.current;
    if (!pane || !sheet || !box) return;
    if (showCrop) {
      pane.scrollTo?.({ top: 0, left: 0, behavior: "auto" });
      return;
    }
    const source = sheet.getBoundingClientRect();
    if (!source.width || !source.height) return;
    const frame = pane.getBoundingClientRect();
    pane.scrollTo?.({
      top: Math.max(
        0,
        pane.scrollTop +
          source.top -
          frame.top +
          ((box[1] + box[3]) / 2) * source.height -
          pane.clientHeight / 2,
      ),
      left: Math.max(
        0,
        pane.scrollLeft +
          source.left -
          frame.left +
          ((box[0] + box[2]) / 2) * source.width -
          pane.clientWidth / 2,
      ),
      behavior: "auto",
    });
  }
  useEffect(() => {
    revealSource();
    // Only navigation reveals a region; text edits and background refreshes keep manual pan.
  }, [issue.id, index, src, showCrop]);
  function selectRegion(id: string, next: number) {
    setSelected({ id, index: next });
    onSelect?.(id);
  }
  return (
    <figure className="review-source-focus">
      <figcaption>
        <div className="review-source-title">
          <strong>原件 · 第 {issue.page} 页</strong>
          <small>
            {box
              ? issue.image_precision === "line"
                ? "行级定位"
                : "区域定位"
              : "未可靠定位"}
          </small>
        </div>
        <div className="review-source-tools">
          {box && (
            <div className="review-source-modes" aria-label="原图查看范围">
              <button
                type="button"
                aria-pressed={!showCrop}
                onClick={() => setCrop(false)}
              >
                整页
              </button>
              <button
                type="button"
                aria-pressed={showCrop}
                onClick={() => setCrop(true)}
              >
                当前区域
              </button>
            </div>
          )}
          <label>
            <span className="sr-only">原图放大倍数</span>
            <input
              aria-label="原图放大倍数"
              type="range"
              min="100"
              max="300"
              step="25"
              value={zoom}
              onChange={(e) => setZoom(Number(e.target.value))}
            />
            <output>{zoom}%</output>
          </label>
          <a
            href={src}
            target="_blank"
            rel="noreferrer"
            aria-label={`打开完整原图 · 第 ${issue.page} 页`}
            title="打开完整原图"
          >
            ↗
          </a>
        </div>
      </figcaption>
      {box && issue.boxes.length > 1 && (
        <nav className="review-source-positions" aria-label="本疑点的原图位置">
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
      <div
        className="review-source-viewport"
        ref={viewport}
        aria-label="原图查看区"
        tabIndex={0}
      >
        {box && context && showCrop && (
          <svg
            className="review-source-crop"
            aria-label="疑点局部原图"
            data-review-id={issue.id}
            data-box-index={index}
            viewBox={`${context[0]} ${context[1]} ${context[2] - context[0]} ${context[3] - context[1]}`}
            preserveAspectRatio="none"
            style={{
              width: `${zoom}%`,
              aspectRatio:
                ((context[2] - context[0]) * ratio) / (context[3] - context[1]),
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
              data-precision={issue.image_precision}
              x={box[0]}
              y={box[1]}
              width={box[2] - box[0]}
              height={box[3] - box[1]}
            />
          </svg>
        )}
        <div
          className="review-source-sheet"
          ref={original}
          hidden={showCrop}
          style={{ width: `${zoom}%` }}
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
          onPointerCancel={() => setBegin(null)}
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
            if (b[2] - b[0] > 0.005 && b[3] - b[1] > 0.005) {
              onBox(b);
              setCrop(true);
            }
          }}
        >
          <img
            src={src}
            draggable={false}
            alt={`原书第 ${issue.page} 页`}
            onLoad={(e) => {
              setRatio(
                e.currentTarget.naturalWidth / e.currentTarget.naturalHeight,
              );
              revealSource();
            }}
          />
          <svg
            viewBox="0 0 1 1"
            preserveAspectRatio="none"
            aria-label="原图疑点框"
          >
            {locations.flatMap((item, locationIndex) =>
              item.page === issue.page && item.image_precision !== "page"
                ? item.boxes.map((b, i) => (
                    <rect
                      key={item.id + ":" + i}
                      role="button"
                      tabIndex={0}
                      aria-label={`原图疑点 ${locationIndex + 1}，位置 ${i + 1}`}
                      data-review-id={item.id}
                      data-box-index={i}
                      data-precision={item.image_precision}
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
      </div>
      <p className="review-source-caption">
        {!box
          ? "本页没有可靠的局部定位，请核对整页。"
          : showCrop
            ? "当前区域来自原图，可切回整页查看上下文。"
            : "拖框查看局部；放大后可滚动查看。"}
      </p>
    </figure>
  );
}
