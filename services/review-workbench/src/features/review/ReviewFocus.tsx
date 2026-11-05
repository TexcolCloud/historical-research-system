import { Button } from "@/components/ui/button.tsx";
import {
  decisionRange,
  type ReviewLocation,
} from "@/editor/review-highlights.ts";

// Retain historical draft values; new terminal review offers keep or edit only.
export type Resolution = {
  id: string;
  action: "keep" | "adopt" | "edit" | "defer";
};

export default function ReviewFocus({
  locations,
  active,
  onSelect,
  text,
  resolutions,
  onResolve,
  onEdit,
  disabled,
  editing = false,
}: {
  locations: ReviewLocation[];
  active: string;
  onSelect: (id: string) => void;
  text: string;
  resolutions: Resolution[];
  onResolve: (action: Resolution["action"]) => void;
  onEdit: () => void;
  disabled: boolean;
  editing?: boolean;
}) {
  const index = locations.findIndex((l) => l.id === active),
    issue = locations[index];
  if (!issue) return null;
  const range = decisionRange(text, issue);
  return (
    <section className="review-focus" aria-label="当前疑点">
      <div className="review-step-heading">
        <strong>
          疑点 {index + 1} / {locations.length}
        </strong>
        <span>{editing ? "正在修改" : "对照原图，作出最终判断"}</span>
      </div>
      {locations.length > 1 && (
        <nav className="review-focus-index" aria-label="本页疑点">
          {locations.map((l, i) => (
            <Button
              key={l.id}
              size="sm"
              variant={l.id === active ? "default" : "outline"}
              disabled={disabled || editing}
              onClick={() => onSelect(l.id)}
              aria-pressed={l.id === active}
            >
              {i + 1} ·{" "}
              {resolutions.some((r) => r.id === l.id && r.action !== "defer")
                ? "已处理"
                : "待核"}
            </Button>
          ))}
        </nav>
      )}
      <div className="review-reading-pair">
        <div>
          <small>
            {range ? "当前待核文字" : "原识别片段 · 请结合整页核对"}
          </small>
          <mark>
            {range
              ? range.start === range.end
                ? "▏（此处缺字）"
                : text.slice(range.start, range.end)
              : issue.candidate || issue.anchor.exact || "请查看整页"}
          </mark>
        </div>
        {issue.source_reading && (
          <div>
            <small>机器参考读法 · 由你裁决</small>
            <span>{issue.source_reading}</span>
          </div>
        )}
      </div>
      <details className="review-observation">
        <summary>为什么需要核对</summary>
        <p>{issue.message || "请核对这处文字与原图是否一致。"}</p>
      </details>
      {!range && (
        <p role="status">
          {issue.boxes.length
            ? "原图区域已定位，正文暂未可靠定位。"
            : "正文与原图暂未可靠定位。"}
          请结合原识别片段核对整页；需要修改时可编辑整页 Markdown。
        </p>
      )}
      {range && !issue.boxes.length && (
        <p className="platform-inline-status">
          正文已定位，原图需结合整页核对。
        </p>
      )}
      {!editing && (
        <div className="review-judgment-actions">
          <Button disabled={disabled} onClick={() => onResolve("keep")}>
            识别正确，保留
          </Button>
          <Button variant="outline" disabled={disabled} onClick={onEdit}>
            需要修改
          </Button>
        </div>
      )}
    </section>
  );
}
