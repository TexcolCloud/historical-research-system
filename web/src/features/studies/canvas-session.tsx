import { useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation, useSearchParams } from "react-router-dom";
import type { Edge, ReactFlowInstance } from "@xyflow/react";
import { Button } from "@/components/ui/button";
import { studies, studyKeys } from "./api";
import { recordLabels } from "./StudyRecords";
import { CanvasMaterialDrawer, type CanvasDrawerPage } from "./canvas-drawer";
import { CanvasToolbar } from "./canvas-toolbar";
import { CanvasConnectionEditor, CanvasInspector } from "./canvas-inspector";
import {
  groupSelection,
  removeSelection,
  orderedCanvasNodes,
  isCanvasNodeHidden,
  reparentDroppedNodes,
  findCanvasPlacement,
  worldPosition,
} from "./canvas-model";
import { downloadCanvasFile, type CanvasLabel } from "./canvas-export";
import { useCanvasDocument } from "./canvas-state";
import { canvasNodeGeometry, type StudyFlowNode } from "./canvas-nodes";
import CanvasFlow from "./canvas-flow";
import type {
  StudyCanvasDocument,
  StudyCanvasRecord,
  StudyCanvasNode,
  StudyRecord,
  StudyRecordKind,
  StudyTopic,
} from "./types";

export default function CanvasSession({
  canvas,
  topic,
  records,
  recordPage,
  workspace,
  createCanvas,
  onArchived,
  management,
}: {
  canvas: StudyCanvasRecord;
  topic: StudyTopic;
  records: StudyRecord[];
  recordPage: CanvasDrawerPage;
  workspace: string;
  createCanvas: (
    title: string,
    document?: StudyCanvasDocument,
  ) => Promise<StudyCanvasRecord | undefined>;
  onArchived: (canvas: StudyCanvasRecord) => void;
  management?: ReactNode;
}) {
  const state = useCanvasDocument(canvas, workspace);
  const cache = useQueryClient();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const [selectedNodes, setSelectedNodes] = useState<string[]>(() =>
    params.get("node") ? [params.get("node")!] : [],
  );
  const [selectedEdges, setSelectedEdges] = useState<string[]>(() =>
    params.get("edge") ? [params.get("edge")!] : [],
  );
  const [focused, setFocused] = useState(params.get("node") || "");
  const [drawer, setDrawer] = useState(true);
  const [editing, setEditing] = useState<StudyRecordKind | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [operationError, setOperationError] = useState("");
  const flow = useRef<ReactFlowInstance<StudyFlowNode, Edge> | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  const session = useRef<HTMLDivElement>(null);
  const readonly = canvas.archived || topic.archived;
  const document = state.document;
  const node = document.nodes.find((item) => item.id === focused);
  const edge = document.edges.find((item) => selectedEdges.includes(item.id));
  const recordId = node?.record_kind === "record" ? node.record_id : "";
  const context = useQuery({
    queryKey: ["studies", "canvas-context", canvas.id, canvas.version],
    queryFn: ({ signal }) =>
      studies.canvasContext(canvas.id, canvas.version, signal),
    enabled: canvas.document.nodes.length > 0,
    retry: false,
    staleTime: 30_000,
  });
  const selectedRecord = useQuery({
    queryKey: ["studies", "record", recordId],
    queryFn: ({ signal }) => studies.record(recordId, signal),
    enabled: !!recordId,
    initialData: records.find((record) => record.id === recordId),
    staleTime: 30_000,
    retry: false,
  });
  const recordMap = useMemo(
    () =>
      new Map(
        [
          ...records,
          ...(context.data?.records || []),
          ...(selectedRecord.data ? [selectedRecord.data] : []),
        ].map((record) => [record.id, record]),
      ),
    [records, context.data?.records, selectedRecord.data],
  );
  const materials = useMemo(
    () =>
      new Map(
        [...topic.materials, ...(context.data?.materials || [])].map(
          (material) => [material.id, material],
        ),
      ),
    [topic.materials, context.data?.materials],
  );

  function focusNode(id: string) {
    setFocused(id);
    setSelectedEdges([]);
    setEditing(null);
    setConnecting(false);
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.set("canvas", canvas.id);
        next.delete("edge");
        id ? next.set("node", id) : next.delete("node");
        return next;
      },
      { replace: true },
    );
  }
  function focusEdge(id: string) {
    setSelectedEdges([id]);
    setSelectedNodes([]);
    setFocused("");
    setEditing(null);
    setConnecting(false);
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.set("canvas", canvas.id);
        next.set("edge", id);
        next.delete("node");
        return next;
      },
      { replace: true },
    );
  }
  function connect(
    source: string,
    target: string,
    relation = "association",
    label = "关联",
  ) {
    if (readonly) return;
    if (
      source === target ||
      !document.nodes.some((node) => node.id === source) ||
      !document.nodes.some((node) => node.id === target)
    ) {
      setOperationError("请选择两张不同的现有卡片建立连线。");
      return;
    }
    const id = crypto.randomUUID();
    state.apply({
      ...document,
      edges: [
        ...document.edges,
        { id, source, target, relation, label, notes: "", source_refs: [] },
      ],
    });
    focusEdge(id);
  }
  function updateNode(id: string, changes: Partial<StudyCanvasNode>) {
    if (readonly) return;
    state.apply({
      ...document,
      nodes: document.nodes.map((item) =>
        item.id === id ? { ...item, ...changes } : item,
      ),
    });
  }
  function deleteSelection() {
    if (readonly) return;
    state.apply(removeSelection(document, selectedNodes, selectedEdges));
    setSelectedNodes([]);
    setSelectedEdges([]);
    focusNode("");
  }
  function addPlacement(
    kind: "source" | "record",
    id: string,
    point?: { x: number; y: number },
  ) {
    if (readonly) return;
    const material = materials.get(id),
      record = recordMap.get(id);
    if (kind === "source" ? !material : !record) return;
    const existing = document.nodes.find(
      (item) => item.record_kind === kind && item.record_id === id,
    );
    if (existing) {
      focusNode(existing.id);
      setSelectedNodes([existing.id]);
      void flow.current?.fitView({
        nodes: [{ id: existing.id }],
        maxZoom: 1,
        padding: 0.5,
      });
      return;
    }
    appendPlacement(
      document,
      kind,
      id,
      material?.title || record?.title || "记录",
      point,
    );
  }

  function appendPlacement(
    current: StudyCanvasDocument,
    kind: "source" | "record",
    recordId: string,
    title: string,
    point?: { x: number; y: number },
  ) {
    const bounds = panel.current?.getBoundingClientRect();
    const left = bounds?.left || 0;
    const top = bounds?.top || 0;
    const width = bounds?.width || window.innerWidth;
    const height = bounds?.height || 580;
    const leftInset = drawer ? 320 : 32;
    const rightInset = 380; // The newly selected card opens the inspector.
    const toCanvas = (position: { x: number; y: number }) =>
      flow.current?.screenToFlowPosition(position) || {
        x: (position.x - left - current.viewport.x) / current.viewport.z,
        y: (position.y - top - current.viewport.y) / current.viewport.z,
      };
    const origin = toCanvas({ x: left + leftInset, y: top + 32 });
    const end = toCanvas({
      x: left + width - rightInset,
      y: top + height - 32,
    });
    const size = { width: 280, height: 250 };
    const position = point || findCanvasPlacement(current, origin, size, end.x);
    const idNode = crypto.randomUUID();
    const next = {
      ...current,
      nodes: [
        ...current.nodes,
        {
          id: idNode,
          record_kind: kind,
          record_id: recordId,
          title,
          ...position,
          ...size,
        },
      ],
    };
    state.apply(point ? reparentDroppedNodes(next, [idNode]) : next);
    setSelectedNodes([idNode]);
    focusNode(idNode);
    if (
      !point &&
      (position.x + size.width > end.x || position.y + size.height > end.y)
    ) {
      const visible = current.nodes.filter((node) => {
        if (node.record_kind === "group" || isCanvasNodeHidden(current, node))
          return false;
        const location = worldPosition(current, node);
        return (
          location.x + node.width > origin.x &&
          location.x < end.x &&
          location.y + node.height > origin.y &&
          location.y < end.y
        );
      });
      window.requestAnimationFrame(() => {
        void flow.current?.fitView({
          nodes: [...visible.map((node) => ({ id: node.id })), { id: idNode }],
          padding: {
            left: `${leftInset}px`,
            right: `${rightInset}px`,
            top: "32px",
            bottom: "32px",
          },
          maxZoom: Math.min(current.viewport.z, 1),
          duration: 180,
        });
      });
    }
  }
  const labels = useMemo(
    () =>
      new Map(
        document.nodes.map((item): [string, CanvasLabel] => {
          const source =
            item.record_kind === "source"
              ? materials.get(item.record_id)
              : undefined;
          const record =
            item.record_kind === "record"
              ? recordMap.get(item.record_id)
              : undefined;
          return [
            item.id,
            {
              title:
                source?.title || record?.title || item.title || "未命名分组",
              kind: source
                ? "史料 · 只读来源"
                : record
                  ? recordLabels[record.kind]
                  : item.record_kind === "group"
                    ? "分组"
                    : "记录",
              text: source?.preview || record?.text || "",
              meta: source
                ? `来源卡 v${source.card_version}`
                : record
                  ? `${record.origin === "ai_suggestion" ? "AI 建议采纳" : "个人记录"} · v${record.version}`
                  : "",
            },
          ];
        }),
      ),
    [document.nodes, materials, recordMap],
  );
  const actions = useRef({
    document,
    updateNode,
    focusNode,
    beginGesture: state.beginGesture,
    endGesture: state.endGesture,
  });
  actions.current = {
    document,
    updateNode,
    focusNode,
    beginGesture: state.beginGesture,
    endGesture: state.endGesture,
  };
  // Stable callbacks let unchanged rich cards skip renders while their neighbours move.
  const nodeActions = useMemo(
    () => ({
      onToggle: (id: string) => {
        const node = actions.current.document.nodes.find(
          (item) => item.id === id,
        );
        if (node)
          actions.current.updateNode(id, { collapsed: !node.collapsed });
      },
      onOpen: (id: string) => {
        setSelectedNodes([id]);
        actions.current.focusNode(id);
      },
      beginGesture: () => actions.current.beginGesture(),
      endGesture: () => actions.current.endGesture(),
    }),
    [],
  );
  const nodes: StudyFlowNode[] = orderedCanvasNodes(document).map((item) => {
    const source =
        item.record_kind === "source"
          ? materials.get(item.record_id)
          : undefined,
      record =
        item.record_kind === "record"
          ? recordMap.get(item.record_id)
          : undefined;
    return {
      id: item.id,
      type: "study-card",
      position: { x: item.x, y: item.y },
      ...canvasNodeGeometry(item),
      parentId: item.parent_id || undefined,
      selected: selectedNodes.includes(item.id),
      hidden: isCanvasNodeHidden(document, item),
      dragHandle: ".study-card-drag",
      zIndex: item.record_kind === "group" ? -1 : 1,
      data: {
        ...labels.get(item.id)!,
        recordKind: item.record_kind,
        noteKind:
          item.record_kind === "group"
            ? "group"
            : source
              ? "source"
              : record?.kind || "note",
        sourceCurrent: source?.source_current !== false,
        collapsed: !!item.collapsed,
        ...nodeActions,
      },
    };
  });
  async function savedRecord(record: StudyRecord) {
    cache.setQueryData(["studies", "record", record.id], record);
    await cache.invalidateQueries({ queryKey: studyKeys.records(topic.id) });
    setEditing(null);
    const current = actions.current.document;
    if (
      !current.nodes.some(
        (item) => item.record_kind === "record" && item.record_id === record.id,
      )
    ) {
      appendPlacement(current, "record", record.id, record.title);
    }
  }
  return (
    <div
      className="study-canvas-session"
      ref={session}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget))
          void state.flush();
      }}
      onKeyDown={(event) => {
        const target = event.target;
        if (
          target instanceof HTMLElement &&
          (target.closest("input,textarea,select,[contenteditable=true]") ||
            readonly)
        )
          return;
        if (
          (event.ctrlKey || event.metaKey) &&
          event.key.toLowerCase() === "z"
        ) {
          event.preventDefault();
          event.shiftKey ? state.redo() : state.undo();
        } else if (
          (event.ctrlKey || event.metaKey) &&
          event.key.toLowerCase() === "y"
        ) {
          event.preventDefault();
          state.redo();
        } else if (["Delete", "Backspace"].includes(event.key)) {
          event.preventDefault();
          deleteSelection();
        }
      }}
    >
      <CanvasToolbar
        management={management}
        canvas={canvas}
        topicArchived={topic.archived}
        readonly={readonly}
        state={state}
        labels={labels}
        drawer={drawer}
        onDrawer={() => setDrawer(!drawer)}
        onRecord={(kind) => {
          focusNode("");
          setSelectedNodes([]);
          setEditing(kind);
        }}
        canGroup={!!selectedNodes.length}
        canConnect={
          document.nodes.filter((node) => node.record_kind !== "group").length >
          1
        }
        onConnect={() => {
          setConnecting(true);
          setEditing(null);
        }}
        onGroup={() => {
          const id = crypto.randomUUID();
          state.apply(groupSelection(document, selectedNodes, id));
          setSelectedNodes([id]);
          focusNode(id);
        }}
        createCanvas={createCanvas}
        onArchived={onArchived}
        onFullscreen={() => {
          if (window.document.fullscreenElement)
            void window.document.exitFullscreen();
          else
            void session.current
              ?.requestFullscreen()
              .catch(() =>
                setOperationError("浏览器未允许全屏，可以继续在当前窗口整理。"),
              );
        }}
      />
      {(canvas.archived || topic.archived) && (
        <p className="study-canvas-notice">
          {topic.archived
            ? "专题已归档，恢复专题后可继续整理。"
            : "画布已归档，可在画布管理中恢复。"}
        </p>
      )}
      {state.notice && <p className="study-canvas-notice">{state.notice}</p>}
      {context.isError && (
        <p role="alert" className="study-canvas-notice">
          画布来源摘要暂时无法读取：{context.error.message}
          <Button variant="outline" onClick={() => void context.refetch()}>
            重新读取摘要
          </Button>
        </p>
      )}
      {(state.error || state.backupError || operationError) && (
        <div className="study-canvas-error" role="alert">
          <p>{state.error || state.backupError || operationError}</p>
          {state.error && !state.conflict && (
            <Button
              variant="outline"
              disabled={state.saving}
              onClick={() => void state.save()}
            >
              重试保存
            </Button>
          )}
          {state.conflict && (
            <>
              <Button
                variant="outline"
                onClick={() =>
                  void createCanvas(`${canvas.title} · 恢复草稿`, document)
                }
              >
                另存当前草稿
              </Button>
              <Button variant="outline" onClick={() => void state.loadServer()}>
                保留草稿并载入服务器版本
              </Button>
            </>
          )}
        </div>
      )}
      {state.retainedDraft && (
        <Button
          variant="outline"
          onClick={() =>
            downloadCanvasFile(
              new Blob([JSON.stringify(state.retainedDraft, null, 2)], {
                type: "application/json",
              }),
              `${canvas.title}-此前草稿.json`,
            )
          }
        >
          导出此前草稿
        </Button>
      )}
      <div
        className="study-canvas-stage"
        ref={panel}
        data-drawer={drawer}
        data-inspecting={!!(node || edge || editing || connecting)}
      >
        {drawer && (
          <CanvasMaterialDrawer
            topic={topic}
            records={[...recordMap.values()]}
            document={document}
            readonly={readonly}
            onClose={() => setDrawer(false)}
            onPlace={addPlacement}
            pagination={recordPage}
          />
        )}
        <div
          className="study-canvas-surface"
          role="region"
          aria-label="自由研究画布"
          tabIndex={0}
          onDragOver={(event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = "copy";
          }}
          onDrop={(event) => {
            event.preventDefault();
            try {
              const value = JSON.parse(
                event.dataTransfer.getData("application/x-hrs-study"),
              );
              if (
                (value.kind === "source" || value.kind === "record") &&
                typeof value.id === "string"
              )
                addPlacement(
                  value.kind,
                  value.id,
                  flow.current?.screenToFlowPosition({
                    x: event.clientX,
                    y: event.clientY,
                  }),
                );
            } catch {
              setOperationError(
                "拖入内容不是本专题材料，请从材料抽屉重新选择。",
              );
            }
          }}
        >
          <CanvasFlow
            state={state}
            nodes={nodes}
            selectedEdges={selectedEdges}
            readonly={readonly}
            flow={flow}
            onNodeSelection={setSelectedNodes}
            onEdgeSelection={setSelectedEdges}
            onNode={(id, edit) => {
              focusNode(id);
              if (edit && !readonly) {
                const placement = document.nodes.find((item) => item.id === id);
                const record = placement && recordMap.get(placement.record_id);
                if (record) setEditing(record.kind);
              }
            }}
            onEdge={focusEdge}
            onClear={() => {
              setSelectedNodes([]);
              setSelectedEdges([]);
              focusNode("");
            }}
            onConnect={connect}
          />
          {!document.nodes.length && (
            <div className="study-canvas-welcome">
              <h2>从一张史料或一个问题开始</h2>
              <p>拖入左侧材料，或在上方添加观点、问题和札记。</p>
            </div>
          )}
          <div className="study-canvas-count">
            {
              document.nodes.filter((node) => node.record_kind !== "group")
                .length
            }{" "}
            张卡片 · {document.edges.length} 条研究连线
            <small>拖动顶部移动 · Ctrl / ⌘ 点选顶部多选</small>
          </div>
        </div>
        {connecting ? (
          <CanvasConnectionEditor
            nodes={document.nodes}
            labels={labels}
            selected={selectedNodes}
            onConnect={connect}
            onClose={() => setConnecting(false)}
          />
        ) : (
          <CanvasInspector
            key={node?.id || edge?.id || editing || "empty"}
            topic={topic}
            document={document}
            node={node}
            edge={edge}
            record={selectedRecord.data}
            recordError={selectedRecord.error?.message}
            onRetryRecord={() => void selectedRecord.refetch()}
            readonly={readonly}
            editing={editing}
            onEdit={setEditing}
            onSaved={(record) => void savedRecord(record)}
            onClose={() => {
              focusNode("");
              setSelectedNodes([]);
              setSelectedEdges([]);
              setEditing(null);
            }}
            onRemove={deleteSelection}
            onUpdate={state.apply}
            returnTo={`${location.pathname}${location.search}`}
          />
        )}
      </div>
    </div>
  );
}
