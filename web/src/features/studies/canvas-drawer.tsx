import { useState } from "react";
import { Link } from "react-router-dom";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { recordLabels } from "./StudyRecords";
import type { StudyCanvasDocument, StudyRecord, StudyTopic } from "./types";

export type CanvasDrawerPage = {
  hasMore: boolean;
  loading: boolean;
  onMore: () => void;
  error?: string;
};

export function CanvasMaterialDrawer({
  topic,
  records,
  document,
  readonly,
  onClose,
  onPlace,
  pagination,
}: {
  topic: StudyTopic;
  records: StudyRecord[];
  document: StudyCanvasDocument;
  readonly: boolean;
  onClose: () => void;
  onPlace: (kind: "source" | "record", id: string) => void;
  pagination: CanvasDrawerPage;
}) {
  const [filter, setFilter] = useState("");
  const [kind, setKind] = useState("all");
  const sources = topic.materials
    .filter((item) => ["all", "source"].includes(kind))
    .map((item) => ({
      id: item.id,
      kind: "source" as const,
      label: "史料",
      title: item.title,
      preview: item.preview || `来源卡 v${item.card_version}`,
    }));
  const notes = records
    .filter((item) => !item.archived && (kind === "all" || item.kind === kind))
    .map((item) => ({
      id: item.id,
      kind: "record" as const,
      label: recordLabels[item.kind],
      title: item.title,
      preview: item.text.slice(0, 100),
    }));
  const entries = [...sources, ...notes].filter((item) =>
    `${item.title} ${item.preview}`
      .toLocaleLowerCase()
      .includes(filter.toLocaleLowerCase()),
  );
  const placed = new Set(
    document.nodes.map((node) => `${node.record_kind}:${node.record_id}`),
  );
  return (
    <aside className="study-material-drawer" aria-label="画布材料抽屉">
      <header>
        <h2>材料抽屉</h2>
        <button type="button" aria-label="关闭材料抽屉" onClick={onClose}>
          <X size={17} />
        </button>
      </header>
      <label className="sr-only" htmlFor="canvas-material-search">
        搜索画布材料
      </label>
      <input
        id="canvas-material-search"
        placeholder="检索标题与个人记录"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
      />
      <select
        aria-label="画布材料类型"
        value={kind}
        onChange={(event) => setKind(event.target.value)}
      >
        <option value="all">全部材料</option>
        <option value="source">史料</option>
        {Object.entries(recordLabels).map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
      {!entries.length && (
        <p>
          当前没有匹配材料。
          <Link to={`/studies?topic=${encodeURIComponent(topic.id)}`}>
            前往专题选材
          </Link>
        </p>
      )}
      {entries.map((item) => {
        const exists = placed.has(`${item.kind}:${item.id}`);
        return (
          <article
            key={`${item.kind}:${item.id}`}
            draggable={!readonly}
            onDragStart={(event) => {
              event.dataTransfer.setData(
                "application/x-hrs-study",
                JSON.stringify({ kind: item.kind, id: item.id }),
              );
              event.dataTransfer.effectAllowed = "copy";
            }}
          >
            <span>{item.label}</span>
            <h3>{item.title}</h3>
            <p>{item.preview}</p>
            <Button
              variant={exists ? "ghost" : "outline"}
              size="sm"
              disabled={readonly}
              aria-label={`${exists ? "定位画布" : "放入画布"} ${item.title}`}
              onClick={() => onPlace(item.kind, item.id)}
            >
              {exists ? "已放置 · 定位" : "放入画布"}
            </Button>
          </article>
        );
      })}
      {pagination.error && <p role="alert">{pagination.error}</p>}
      {pagination.hasMore && (
        <Button
          variant="outline"
          disabled={pagination.loading}
          onClick={pagination.onMore}
        >
          {pagination.loading ? "正在读取更多记录…" : "加载更多个人记录"}
        </Button>
      )}
    </aside>
  );
}
