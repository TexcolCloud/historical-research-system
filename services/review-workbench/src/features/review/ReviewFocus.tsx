import { Button } from "@/components/ui/button.tsx";
import {
  resolveLocation,
  type ReviewLocation,
} from "@/editor/review-highlights.ts";
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
}: {
  locations: ReviewLocation[];
  active: string;
  onSelect: (id: string) => void;
  text: string;
  resolutions: Resolution[];
  onResolve: (action: Resolution["action"], replacement?: string) => void;
  onEdit: () => void;
  disabled: boolean;
}) {
  const index = locations.findIndex((l) => l.id === active),
    issue = locations[index];
  if (!issue) return null;
  const located = resolveLocation(text, issue);
  return (
    <section className="review-focus" aria-label="逐项疑点核对">
      <div className="platform-review-actions">
        <Button
          variant="outline"
          disabled={index <= 0}
          onClick={() => onSelect(locations[index - 1].id)}
        >
          上一疑点
        </Button>
        <strong>
          疑点 {index + 1} / {locations.length}
        </strong>
        <Button
          variant="outline"
          disabled={index >= locations.length - 1}
          onClick={() => onSelect(locations[index + 1].id)}
        >
          下一疑点
        </Button>
      </div>
      <div className="review-focus-index">
        {locations.map((l, i) => (
          <Button
            key={l.id}
            variant={l.id === active ? "default" : "outline"}
            onClick={() => onSelect(l.id)}
            aria-pressed={l.id === active}
          >
            {i + 1} ·{" "}
            {resolutions.some((r) => r.id === l.id && r.action !== "defer")
              ? "已处理"
              : "待核"}
          </Button>
        ))}
      </div>
      <p>{issue.location}</p>
      <p>{issue.message || "请对照原件核对本处。"}</p>
      <p>
        当前识别：
        <mark>
          {located && located.start===located.end ? '▏（此处缺字）' : located
            ? text.slice(located.start, located.end)
            : issue.focus || issue.candidate || "未定位"}
        </mark>
      </p>
      {issue.source_reading && (
        <p>审核读法（待核实）：{issue.source_reading}</p>
      )}
      {!located && (
        <p role="status">
          文字位置需重新确认：匹配缺失、重复或已被修改。可在源码中定位。
        </p>
      )}
      <div className="platform-review-actions">
        <Button
          variant="outline"
          disabled={disabled}
          onClick={() => onResolve("keep")}
        >
          保留当前识别
        </Button>
        <Button
          variant="outline"
          disabled={disabled || !located || !issue.suggestion}
          onClick={() => onResolve("adopt", issue.suggestion)}
        >
          采用建议并编辑
        </Button>
        <Button variant="outline" disabled={disabled} onClick={onEdit}>
          定位并自行修改
        </Button>
        <Button
          variant="outline"
          disabled={disabled}
          onClick={() => onResolve("edit")}
        >
          本处修改已完成
        </Button>
        <Button
          variant="outline"
          disabled={disabled}
          onClick={() => onResolve("defer")}
        >
          本处暂不确定
        </Button>
      </div>
    </section>
  );
}
