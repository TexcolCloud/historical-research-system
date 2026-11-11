import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { RenderedMarkdown } from "@/editor/RenderedMarkdown";
import Evidence from "@/features/research/ResearchEvidence";
import EvidenceOriginal from "@/features/research/EvidenceOriginal";
import { studies, studyKeys } from "./api";
import { AdoptedAnalysisLink, RecordEditor } from "./StudyRecords";
import { ungroupSelection } from "./canvas-model";
import type { CanvasLabel } from "./canvas-export";
import type {
  StudyCanvasDocument,
  StudyCanvasEdge,
  StudyCanvasNode,
  StudyRecord,
  StudyRecordKind,
  StudyTopic,
} from "./types";

const relations = {
  association: "关联",
  supports: "支持",
  counters: "反证",
  limits: "限制",
  background: "背景",
  cites: "引自",
  unresolved: "待核关联",
};

export function CanvasConnectionEditor({
  nodes,
  labels,
  selected,
  onConnect,
  onClose,
}: {
  nodes: StudyCanvasNode[];
  labels: Map<string, CanvasLabel>;
  selected: string[];
  onConnect: (
    source: string,
    target: string,
    relation: string,
    label: string,
  ) => void;
  onClose: () => void;
}) {
  const cards = nodes.filter((node) => node.record_kind !== "group");
  const cardIds = new Set(cards.map((node) => node.id));
  const selectedCards = selected.filter((id) => cardIds.has(id));
  const [source, setSource] = useState(selectedCards[0] || cards[0]?.id || "");
  const [target, setTarget] = useState(
    selectedCards[1] || cards.find((node) => node.id !== source)?.id || "",
  );
  const [relation, setRelation] = useState("association");
  const [error, setError] = useState("");
  return (
    <aside className="study-canvas-inspector" aria-label="创建研究连线">
      <header>
        <h2>建立研究连线</h2>
        <button type="button" aria-label="取消建立连线" onClick={onClose}>
          <X size={17} />
        </button>
      </header>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (
            !cardIds.has(source) ||
            !cardIds.has(target) ||
            source === target
          ) {
            setError("请选择两张不同的卡片。");
            return;
          }
          onConnect(
            source,
            target,
            relation,
            relations[relation as keyof typeof relations],
          );
        }}
      >
        <label>
          起点卡片
          <select
            aria-label="连线起点卡片"
            value={source}
            onChange={(event) => setSource(event.target.value)}
          >
            {cards.map((node) => (
              <option key={node.id} value={node.id}>
                {labels.get(node.id)?.title || node.title || "卡片"}
              </option>
            ))}
          </select>
        </label>
        <label>
          终点卡片
          <select
            aria-label="连线终点卡片"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
          >
            {cards.map((node) => (
              <option key={node.id} value={node.id}>
                {labels.get(node.id)?.title || node.title || "卡片"}
              </option>
            ))}
          </select>
        </label>
        <label>
          关系类型
          <select
            aria-label="新连线关系类型"
            value={relation}
            onChange={(event) => setRelation(event.target.value)}
          >
            {Object.entries(relations).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <p>建立后可填写关系说明，并挂接具体原文依据。</p>
        {error && <p role="alert">{error}</p>}
        <Button>建立连线</Button>
      </form>
    </aside>
  );
}

export function CanvasInspector({
  topic,
  document,
  node,
  edge,
  record,
  recordError,
  onRetryRecord,
  readonly,
  editing,
  onEdit,
  onSaved,
  onClose,
  onRemove,
  onUpdate,
  returnTo,
}: {
  topic: StudyTopic;
  document: StudyCanvasDocument;
  node?: StudyCanvasNode;
  edge?: StudyCanvasEdge;
  record?: StudyRecord;
  recordError?: string;
  onRetryRecord: () => void;
  readonly: boolean;
  editing: StudyRecordKind | null;
  onEdit: (kind: StudyRecordKind | null) => void;
  onSaved: (record: StudyRecord) => void;
  onClose: () => void;
  onRemove: () => void;
  onUpdate: (document: StudyCanvasDocument) => void;
  returnTo: string;
}) {
  const [preview, setPreview] = useState("");
  if (!node && !edge && !editing) return null;
  function updateNode(changes: Partial<StudyCanvasNode>) {
    if (readonly || !node) return;
    onUpdate({
      ...document,
      nodes: document.nodes.map((item) =>
        item.id === node.id ? { ...item, ...changes } : item,
      ),
    });
  }
  function updateEdge(changes: Partial<StudyCanvasEdge>) {
    if (readonly || !edge) return;
    onUpdate({
      ...document,
      edges: document.edges.map((item) =>
        item.id === edge.id ? { ...item, ...changes } : item,
      ),
    });
  }
  return (
    <aside className="study-canvas-inspector" aria-label="画布选中内容">
      <header>
        <h2>
          {editing
            ? "编辑个人记录"
            : node?.record_kind === "source"
              ? "原文依据"
              : edge
                ? "个人研究连线"
                : "所选卡片"}
        </h2>
        <button type="button" aria-label="关闭画布详情" onClick={onClose}>
          <X size={17} />
        </button>
      </header>
      {preview ? (
        <>
          <Button variant="outline" onClick={() => setPreview("")}>
            返回连线说明
          </Button>
          <SourceInspector
            topicId={topic.id}
            materialId={preview}
            returnTo={returnTo}
          />
        </>
      ) : editing ? (
        node?.record_kind === "record" && !record ? (
          recordError ? (
            <p role="alert">
              {recordError}
              <Button onClick={onRetryRecord}>重新读取记录</Button>
            </p>
          ) : (
            <p role="status">正在读取完整记录…</p>
          )
        ) : (
          <RecordEditor
            key={record?.id || editing}
            topic={topic}
            record={node?.record_kind === "record" ? record : undefined}
            kind={editing}
            onSaved={onSaved}
            onCancel={() => onEdit(null)}
          />
        )
      ) : node?.record_kind === "source" ? (
        <SourceInspector
          topicId={topic.id}
          materialId={node.record_id}
          returnTo={returnTo}
        />
      ) : node?.record_kind === "group" ? (
        <>
          <label>
            分组名称
            <input
              aria-label="分组名称"
              value={node.title || ""}
              disabled={readonly}
              onChange={(event) => updateNode({ title: event.target.value })}
            />
          </label>
          <Button
            variant="outline"
            disabled={readonly}
            onClick={() => updateNode({ collapsed: !node.collapsed })}
          >
            {node.collapsed ? "展开分组" : "折叠分组"}
          </Button>
          <Button
            variant="outline"
            disabled={readonly}
            onClick={() => {
              onUpdate(ungroupSelection(document, [node.id]));
              onClose();
            }}
          >
            取消分组并保留卡片
          </Button>
        </>
      ) : node?.record_kind === "record" ? (
        recordError ? (
          <p role="alert">
            {recordError}
            <Button onClick={onRetryRecord}>重新读取记录</Button>
          </p>
        ) : record ? (
          <>
            <p className="study-canvas-origin">
              {record.origin === "ai_adopted"
                ? "AI 建议采纳 · 个人记录"
                : "个人研究记录"}
            </p>
            <h3>{record.title}</h3>
            <RenderedMarkdown markdown={record.text} />
            {record.limitations && <p>使用限制：{record.limitations}</p>}
            <AdoptedAnalysisLink record={record} />
            <Button disabled={readonly} onClick={() => onEdit(record.kind)}>
              编辑记录
            </Button>
          </>
        ) : (
          <p role="status">正在读取记录…</p>
        )
      ) : edge ? (
        <>
          <label>
            关系类型
            <select
              aria-label="画布关系类型"
              value={edge.relation}
              disabled={readonly}
              onChange={(event) =>
                updateEdge({
                  relation: event.target.value,
                  label:
                    relations[event.target.value as keyof typeof relations],
                })
              }
            >
              {!Object.hasOwn(relations, edge.relation) && (
                <option value={edge.relation}>{edge.relation}</option>
              )}
              {Object.entries(relations).map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            连线标签
            <input
              aria-label="连线标签"
              value={edge.label}
              disabled={readonly}
              onChange={(event) => updateEdge({ label: event.target.value })}
            />
          </label>
          <label>
            关系说明
            <textarea
              aria-label="关系说明"
              value={edge.notes || ""}
              disabled={readonly}
              onChange={(event) => updateEdge({ notes: event.target.value })}
            />
          </label>
          <fieldset>
            <legend>引用的专题材料</legend>
            {topic.materials.map((material) => (
              <div key={material.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={edge.source_refs?.includes(material.id) || false}
                    disabled={readonly}
                    onChange={(event) =>
                      updateEdge({
                        source_refs: event.target.checked
                          ? [
                              ...new Set([
                                ...(edge.source_refs || []),
                                material.id,
                              ]),
                            ]
                          : edge.source_refs?.filter(
                              (id) => id !== material.id,
                            ),
                      })
                    }
                  />
                  {material.title}
                </label>
                {edge.source_refs?.includes(material.id) && (
                  <Button
                    variant="link"
                    size="sm"
                    onClick={() => setPreview(material.id)}
                  >
                    查看这条依据
                  </Button>
                )}
              </div>
            ))}
          </fieldset>
          <p>这是个人整理的连线，不会修改史料中的实体关系或核验结论。</p>
        </>
      ) : null}
      {!editing && (
        <Button variant="outline" disabled={readonly} onClick={onRemove}>
          移出画布
        </Button>
      )}
    </aside>
  );
}

function SourceInspector({
  topicId,
  materialId,
  returnTo,
}: {
  topicId: string;
  materialId: string;
  returnTo: string;
}) {
  const material = useQuery({
    queryKey: studyKeys.material(topicId, materialId),
    queryFn: ({ signal }) => studies.material(topicId, materialId, signal),
    retry: false,
    staleTime: 30_000,
  });
  if (material.isError)
    return (
      <p role="alert">
        {material.error.message}
        <Button onClick={() => void material.refetch()}>重新读取依据</Button>
      </p>
    );
  if (!material.data) return <p role="status">正在读取保留的来源依据…</p>;
  const value = material.data;
  return (
    <>
      <h3>{value.title}</h3>
      <p>来源卡 v{value.card_version}</p>
      {!value.source_current && (
        <p role="alert">
          来源已有变化，当前保留旧版引用；请核对后明确更新专题选材。
        </p>
      )}
      <Link
        to={`/research?${new URLSearchParams({ card: value.card_id, returnTo })}`}
      >
        打开出处史料卡
      </Link>
      <Evidence
        evidence={value.evidence || []}
        bookId={value.book_id}
        historical={!value.source_current}
      />
      <EvidenceOriginal evidence={value.evidence || []} />
    </>
  );
}
