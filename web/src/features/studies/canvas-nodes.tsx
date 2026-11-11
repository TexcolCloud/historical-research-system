import { memo } from "react";
import {
  Handle,
  NodeResizer,
  Position,
  type Node,
  type NodeProps,
  type NodeHandle,
} from "@xyflow/react";
import {
  BookOpen,
  FileQuestion,
  Lightbulb,
  NotebookPen,
  Layers,
  ChevronDown,
  ChevronRight,
  Quote,
  CalendarDays,
} from "lucide-react";
import type { CanvasLabel } from "./canvas-export";
import type { StudyCanvasNode } from "./types";

const handleSize = 9;
const handleStyle = { width: handleSize, height: handleSize };

/** Known geometry keeps controlled nodes initialized, including offscreen edge endpoints. */
export function canvasNodeGeometry(node: StudyCanvasNode) {
  const width = node.width;
  const height = node.collapsed ? 82 : node.height;
  const handles: NodeHandle[] =
    node.record_kind === "group"
      ? []
      : [
          {
            id: null,
            type: "target",
            position: Position.Left,
            x: -handleSize / 2,
            y: (height - handleSize) / 2,
            width: handleSize,
            height: handleSize,
          },
          {
            id: null,
            type: "source",
            position: Position.Right,
            x: width - handleSize / 2,
            y: (height - handleSize) / 2,
            width: handleSize,
            height: handleSize,
          },
        ];
  return {
    width,
    height,
    measured: { width, height },
    handles,
    style: { width, height },
  };
}

type CanvasNodeData = CanvasLabel & {
  recordKind: "source" | "record" | "group";
  noteKind: string;
  sourceCurrent: boolean;
  collapsed: boolean;
  onToggle: (id: string) => void;
  onOpen: (id: string) => void;
  beginGesture: () => void;
  endGesture: () => void;
};
export type StudyFlowNode = Node<CanvasNodeData, "study-card">;

const icons = {
  source: BookOpen,
  argument: Quote,
  thought: Lightbulb,
  question: FileQuestion,
  note: NotebookPen,
  clue: CalendarDays,
  group: Layers,
};

const CanvasCardNode = memo(
  function CanvasCardNode({ id, data, selected }: NodeProps<StudyFlowNode>) {
    const Icon = icons[data.noteKind as keyof typeof icons] || NotebookPen;
    const group = data.recordKind === "group";
    return (
      <article
        className="study-canvas-card"
        data-kind={data.noteKind}
        data-selected={selected}
        data-stale={!data.sourceCurrent}
        data-group={group}
      >
        <NodeResizer
          isVisible={selected && !data.collapsed}
          minWidth={group ? 300 : 230}
          minHeight={group ? 180 : 170}
          onResizeStart={data.beginGesture}
          onResizeEnd={data.endGesture}
        />
        {!group && (
          <Handle
            type="target"
            position={Position.Left}
            style={handleStyle}
            aria-label="连线终点"
          />
        )}
        <header className="study-card-drag">
          <span>
            <Icon size={14} aria-hidden="true" />
            {data.kind}
          </span>
          {group && <h3 title={data.title}>{data.title}</h3>}
          {group ? (
            <button
              type="button"
              className="nodrag nopan"
              aria-label={`${data.collapsed ? "展开" : "折叠"}分组 ${data.title}`}
              onClick={(event) => {
                event.stopPropagation();
                data.onToggle(id);
              }}
            >
              {data.collapsed ? (
                <ChevronRight size={16} />
              ) : (
                <ChevronDown size={16} />
              )}
            </button>
          ) : (
            <button
              type="button"
              className="nodrag nopan"
              aria-label={`打开 ${data.title}`}
              onClick={(event) => {
                event.stopPropagation();
                data.onOpen(id);
              }}
            >
              打开
            </button>
          )}
        </header>
        {!group && <h3 className="nodrag nopan">{data.title}</h3>}
        {!group && (
          <>
            <div
              className="study-card-body nodrag nopan nowheel"
              tabIndex={0}
              aria-label={`${data.title}内容预览`}
            >
              {data.text ||
                (data.recordKind === "source"
                  ? "打开来源，核对原文与出处。"
                  : "暂无正文，打开后开始整理。")}
            </div>
            <footer className="nodrag nopan">
              {!data.sourceCurrent && <strong>来源已变化 · 待核</strong>}
              <span>{data.meta}</span>
            </footer>
          </>
        )}
        {!group && (
          <Handle
            type="source"
            position={Position.Right}
            style={handleStyle}
            aria-label="连线起点"
          />
        )}
      </article>
    );
  },
  (before, after) =>
    before.selected === after.selected &&
    before.data.title === after.data.title &&
    before.data.text === after.data.text &&
    before.data.kind === after.data.kind &&
    before.data.meta === after.data.meta &&
    before.data.recordKind === after.data.recordKind &&
    before.data.noteKind === after.data.noteKind &&
    before.data.sourceCurrent === after.data.sourceCurrent &&
    before.data.collapsed === after.data.collapsed,
);

export const studyNodeTypes = { "study-card": CanvasCardNode };
