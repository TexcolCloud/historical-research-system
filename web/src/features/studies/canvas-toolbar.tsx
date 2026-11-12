import { useState, type ReactNode } from "react";
import {
  Copy,
  Download,
  Layers,
  Link2,
  Maximize2,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Redo2,
  Undo2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { recordLabels } from "./StudyRecords";
import { studies } from "./api";
import {
  downloadCanvasFile,
  exportCanvasImage,
  type CanvasLabel,
} from "./canvas-export";
import type { useCanvasDocument } from "./canvas-state";
import type {
  StudyCanvasDocument,
  StudyCanvasRecord,
  StudyRecordKind,
} from "./types";

type CanvasState = ReturnType<typeof useCanvasDocument>;
export function CanvasToolbar({
  canvas,
  topicArchived,
  readonly,
  state,
  labels,
  drawer,
  onDrawer,
  onRecord,
  onGroup,
  onConnect,
  canConnect,
  canGroup,
  createCanvas,
  onArchived,
  onFullscreen,
  management,
}: {
  canvas: StudyCanvasRecord;
  topicArchived: boolean;
  readonly: boolean;
  state: CanvasState;
  labels: Map<string, CanvasLabel>;
  drawer: boolean;
  onDrawer: () => void;
  onRecord: (kind: StudyRecordKind) => void;
  onGroup: () => void;
  onConnect: () => void;
  canConnect: boolean;
  canGroup: boolean;
  createCanvas: (
    title: string,
    document?: StudyCanvasDocument,
  ) => Promise<StudyCanvasRecord | undefined>;
  onArchived: (canvas: StudyCanvasRecord) => void;
  onFullscreen: () => void;
  management?: ReactNode;
}) {
  const document = state.document;
  const [rename, setRename] = useState(canvas.title);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");
  async function exportImage(format: "svg" | "png") {
    setExporting(true);
    setError("");
    try {
      await exportCanvasImage(document, labels, canvas.title, format);
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "图片导出未完成，布局仍保留。",
      );
    } finally {
      setExporting(false);
    }
  }
  return (
    <>
      <div className="study-canvas-toolbar">
        <Button variant="outline" aria-expanded={drawer} onClick={onDrawer}>
          {drawer ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
          材料
        </Button>
        {(["thought", "question", "note"] as const).map((kind) => (
          <Button
            key={kind}
            variant="outline"
            disabled={readonly}
            onClick={() => onRecord(kind)}
          >
            <Plus size={14} />
            {recordLabels[kind]}
          </Button>
        ))}
        <Button
          variant="outline"
          disabled={!canConnect || readonly}
          onClick={onConnect}
        >
          <Link2 size={16} />
          连线
        </Button>
        <Button
          variant="outline"
          disabled={!canGroup || readonly}
          onClick={onGroup}
        >
          <Layers size={16} />
          分组
        </Button>
        <Button
          variant="ghost"
          aria-label="撤销画布操作"
          disabled={!state.canUndo || readonly}
          onClick={state.undo}
        >
          <Undo2 size={17} />
        </Button>
        <Button
          variant="ghost"
          aria-label="重做画布操作"
          disabled={!state.canRedo || readonly}
          onClick={state.redo}
        >
          <Redo2 size={17} />
        </Button>
        <span
          className="study-canvas-save"
          role="status"
          data-dirty={state.dirty}
        >
          {state.status}
        </span>
        {state.dirty && !state.conflict && (
          <Button
            variant="ghost"
            disabled={state.saving || readonly}
            onClick={() => void state.save()}
          >
            立即保存
          </Button>
        )}
        <details className="study-canvas-tools">
          <summary>画布管理</summary>
          <div>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void state.updateDetails({
                  title: rename.trim() || canvas.title,
                });
              }}
            >
              <label>
                画布名称
                <input
                  aria-label="重命名画布"
                  value={rename}
                  maxLength={200}
                  onChange={(event) => setRename(event.target.value)}
                />
              </label>
              <Button variant="outline" disabled={state.saving || readonly}>
                保存名称
              </Button>
            </form>
            <Button
              variant="outline"
              disabled={readonly}
              onClick={() =>
                void createCanvas(`${canvas.title} · 副本`, document)
              }
            >
              <Copy size={15} />
              复制布局
            </Button>
            <p>副本布局独立；所引用的史料和个人记录仍为同一份内容。</p>
            <Button
              variant="outline"
              disabled={state.saving || topicArchived}
              onClick={async () => {
                const result = await state.updateDetails({
                  archived: !canvas.archived,
                });
                if (result) onArchived(result);
              }}
            >
              {canvas.archived ? "恢复画布" : "归档画布"}
            </Button>
            {management}
          </div>
        </details>
        <details className="study-canvas-tools">
          <summary>
            <Download size={15} />
            导出
          </summary>
          <div>
            <Button
              variant="outline"
              disabled={exporting}
              onClick={() => void exportImage("png")}
            >
              PNG 图片
            </Button>
            <Button
              variant="outline"
              disabled={exporting}
              onClick={() => void exportImage("svg")}
            >
              SVG 矢量图
            </Button>
            <Button
              variant="outline"
              onClick={() =>
                downloadCanvasFile(
                  new Blob([JSON.stringify(document, null, 2)], {
                    type: "application/json",
                  }),
                  `${canvas.title}-当前布局.json`,
                )
              }
            >
              当前布局备份
            </Button>
            <a
              href={studies.exportURL(canvas.id, "md")}
              aria-disabled={state.dirty}
            >
              已保存的 Markdown 整理稿
            </a>
            <a href={studies.exportURL(canvas.id, "json")}>已保存的完整备份</a>
            {state.dirty && (
              <p>服务端整理稿使用上次成功保存的版本；可先立即保存。</p>
            )}
          </div>
        </details>
        <Button
          variant="ghost"
          aria-label="切换画布全屏"
          onClick={onFullscreen}
        >
          <Maximize2 size={16} />
        </Button>
      </div>
      {error && (
        <p role="alert" className="study-canvas-error">
          {error}
        </p>
      )}
    </>
  );
}
