import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { RenderedMarkdown } from "@/editor/RenderedMarkdown";
import EventNature from "@/features/research/EventNature";
import { studies, studyKeys } from "./api";
import {
  ChooseTopic,
  StudyErrorState,
  TopicChooser,
  useStudyTopic,
} from "./StudyTopicScope";
import { MaterialPreview } from "./StudyTopics";
import type {
  StudyDate,
  StudyRecord,
  StudyRecordKind,
  StudyTopic,
} from "./types";
import "./study-pages.css";

export const recordLabels: Record<StudyRecordKind, string> = {
  argument: "论据",
  thought: "观点",
  question: "问题",
  note: "札记",
  clue: "个人线索",
};

/** Adoption provenance stays reachable even after the personal interpretation is edited. */
export function AdoptedAnalysisLink({
  record,
}: {
  record: Pick<StudyRecord, "origin" | "analysis_id" | "topic_id">;
}) {
  if (record.origin !== "ai_adopted" || !record.analysis_id) return null;
  return (
    <Link
      className="study-adoption-link"
      to={`/analysis?${new URLSearchParams({ topic: record.topic_id, analysis: record.analysis_id })}`}
    >
      查看原分析与采纳依据
    </Link>
  );
}

export function ArgumentsPage() {
  const scope = useStudyTopic();
  const [params, setParams] = useSearchParams();
  const requested = params.get("kind");
  const kind: StudyRecordKind | "all" =
    requested === "all"
      ? "all"
      : requested && Object.hasOwn(recordLabels, requested)
        ? (requested as StudyRecordKind)
        : "argument";
  const [editing, setEditing] = useState<StudyRecord | "new" | null>(null);
  const showArchived = params.get("archived") === "true";
  const offset = Math.max(
    0,
    Math.min(2_000_000_000, Number(params.get("offset")) || 0),
  );
  function changeList(changes: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes))
      value ? next.set(key, value) : next.delete(key);
    setParams(next);
  }
  const setKind = (value: StudyRecordKind | "all") =>
    changeList({ kind: value, offset: "" });
  const setArchived = (value: boolean) =>
    changeList({ archived: String(value), offset: "" });
  const setOffset = (value: number) =>
    changeList({ offset: value ? String(value) : "" });
  const records = useQuery({
    queryKey: [...studyKeys.records(scope.topicId), showArchived, kind, offset],
    queryFn: ({ signal }) =>
      studies.records(scope.topicId, signal, showArchived, {
        offset,
        limit: 20,
        ...(kind === "all" ? {} : { kind }),
      }),
    enabled: !!scope.topicId,
    retry: false,
    staleTime: 30_000,
  });
  const cache = useQueryClient();
  const [source, setSource] = useState("");
  const archive = useMutation({
    mutationFn: (record: StudyRecord) =>
      studies.updateRecord(record.id, {
        expected_version: record.version,
        archived: !record.archived,
      }),
    onSuccess: () =>
      void cache.invalidateQueries({
        queryKey: studyKeys.records(scope.topicId),
      }),
  });
  const items = records.data?.items ?? [];
  useEffect(() => {
    setEditing(null);
    setSource("");
  }, [scope.topicId]);
  return (
    <section className="study-page">
      <header className="study-page-heading">
        <div>
          <p className="study-eyebrow">研究资料</p>
          <h1>论据丛</h1>
          <p>将选材与使用判断整理在一起，个人解释与原文依据分别保留。</p>
        </div>
        <TopicChooser scope={scope} />
      </header>
      {!scope.topicId ? (
        <ChooseTopic />
      ) : scope.topic.isError ? (
        <StudyErrorState
          error={scope.topic.error}
          retry={() => void scope.topic.refetch()}
        />
      ) : !scope.topic.data ? (
        <p role="status">正在读取专题…</p>
      ) : (
        <>
          <div className="study-record-toolbar">
            <label>
              记录类型
              <select
                aria-label="记录类型筛选"
                value={kind}
                onChange={(event) => {
                  setKind(event.target.value as StudyRecordKind | "all");
                  setEditing(null);
                }}
              >
                <option value="all">全部个人记录</option>
                {Object.entries(recordLabels).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="study-check">
              <input
                type="checkbox"
                checked={showArchived}
                onChange={(event) => {
                  setArchived(event.target.checked);
                }}
              />
              查看已归档记录
            </label>
            <Button
              disabled={scope.topic.data.archived}
              onClick={() => setEditing("new")}
            >
              新建{kind === "all" ? "记录" : recordLabels[kind]}
            </Button>
          </div>
          {editing && (
            <RecordEditor
              key={`${scope.topicId}:${editing === "new" ? "new" : editing.id}`}
              topic={scope.topic.data}
              record={editing === "new" ? undefined : editing}
              kind={kind === "all" ? "note" : kind}
              onSaved={() => {
                setEditing(null);
                setOffset(0);
                void cache.invalidateQueries({
                  queryKey: studyKeys.records(scope.topicId),
                });
              }}
              onCancel={() => setEditing(null)}
            />
          )}
          {records.isError ? (
            <StudyErrorState
              error={records.error}
              retry={() => void records.refetch()}
            />
          ) : records.isPending ? (
            <p role="status">正在读取个人记录…</p>
          ) : items.length ? (
            <div className="study-record-grid">
              {items.map((record) => (
                <article key={record.id} className="study-record">
                  <div className="study-detail-heading">
                    <span className="study-badge">
                      {recordLabels[record.kind]} ·{" "}
                      {record.origin === "ai_adopted"
                        ? "采纳的 AI 建议"
                        : "个人记录"}
                    </span>
                    <span>v{record.version}</span>
                  </div>
                  <h2>{record.title}</h2>
                  <RenderedMarkdown markdown={record.text} />
                  {record.limitations && (
                    <div className="study-limitations">
                      <strong>使用限制</strong>
                      <p className="study-preserve">{record.limitations}</p>
                    </div>
                  )}
                  <div className="study-source-links">
                    <AdoptedAnalysisLink record={record} />
                    {record.source_refs.map((ref) => {
                      const material = scope.topic.data!.materials.find(
                        (material) => material.id === ref,
                      );
                      return (
                        <button key={ref} onClick={() => setSource(ref)}>
                          {material?.title || "已保留的来源引用"}
                          {material?.source_current === false && " · 待核"}
                        </button>
                      );
                    })}
                  </div>
                  <div className="study-actions">
                    <Button
                      variant="outline"
                      onClick={() => setEditing(record)}
                    >
                      编辑记录
                    </Button>
                    <Link
                      to={`/studies/${encodeURIComponent(scope.topicId)}/canvas`}
                    >
                      去画布摆放
                    </Link>
                    <Button
                      variant="ghost"
                      disabled={archive.isPending}
                      onClick={() => archive.mutate(record)}
                    >
                      {record.archived ? "恢复记录" : "归档记录"}
                    </Button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="study-empty">
              <h2>此范围还没有记录</h2>
              <p>可以从一条论据开始，引用专题材料并记下其适用范围。</p>
            </div>
          )}
          {archive.isError && <p role="alert">{archive.error.message}</p>}
          {records.data && (
            <nav className="study-actions" aria-label="个人记录分页">
              <Button
                variant="outline"
                disabled={!offset || records.isFetching}
                onClick={() => setOffset(Math.max(0, offset - 20))}
              >
                上一页记录
              </Button>
              <span>
                {records.data.total ? offset + 1 : 0}–
                {Math.min(offset + 20, records.data.total)} /{" "}
                {records.data.total}
              </span>
              <Button
                variant="outline"
                disabled={
                  offset + 20 >= records.data.total || records.isFetching
                }
                onClick={() => setOffset(offset + 20)}
              >
                下一页记录
              </Button>
            </nav>
          )}
          {source && (
            <MaterialPreview
              topicId={scope.topicId}
              materialId={source}
              onClose={() => setSource("")}
            />
          )}
        </>
      )}
    </section>
  );
}

export function RecordEditor({
  topic,
  record,
  kind = "note",
  onSaved,
  onCancel,
}: {
  topic: StudyTopic;
  record?: StudyRecord;
  kind?: StudyRecordKind;
  onSaved: (record: StudyRecord) => void;
  onCancel: () => void;
}) {
  const cache = useQueryClient();
  const workspace = cache.getQueryData<{ workspace_id?: string }>([
    "platform",
    "runtime",
  ])?.workspace_id;
  const draftKey = workspace
    ? `hrs:study-record-draft:${workspace}:${topic.id}:${record?.id || `new:${kind}`}`
    : "";
  const [retained] = useState(() => readDraft(draftKey));
  const [baseVersion] = useState(
    retained?.expected_version ?? record?.version ?? 0,
  );
  const [selectedKind, setKind] = useState(
    retained?.kind || record?.kind || kind,
  );
  const [title, setTitle] = useState(retained?.title ?? record?.title ?? "");
  const [text, setText] = useState(retained?.text ?? record?.text ?? "");
  const [limitations, setLimitations] = useState(
    retained?.limitations ?? record?.limitations ?? "",
  );
  const [refs, setRefs] = useState(
    retained?.source_refs ?? record?.source_refs ?? [],
  );
  const [dateText, setDateText] = useState(
    retained?.date_text ?? record?.date?.text ?? "",
  );
  const [dateNormalized, setDateNormalized] = useState(
    retained?.date_normalized ?? record?.date?.normalized ?? "",
  );
  const [certainty, setCertainty] = useState(
    retained?.certainty ?? record?.date?.certainty ?? "unknown",
  );
  const [eventStatus, setStatus] = useState(
    retained?.event_status ?? record?.event_status ?? "unknown",
  );
  const [preview, setPreview] = useState(false);
  const [draftError, setDraftError] = useState("");
  useEffect(() => {
    if (!draftKey) return;
    const draft: RecordDraft = {
      expected_version: baseVersion,
      kind: selectedKind,
      title,
      text,
      limitations,
      source_refs: refs,
      date_text: dateText,
      date_normalized: dateNormalized,
      certainty,
      event_status: eventStatus,
    };
    try {
      localStorage.setItem(draftKey, JSON.stringify(draft));
    } catch {
      setDraftError(
        "本机草稿暂存失败，当前输入仍在；离开页面前请保存记录或复制正文。",
      );
    }
  }, [
    draftKey,
    baseVersion,
    selectedKind,
    title,
    text,
    limitations,
    refs,
    dateText,
    dateNormalized,
    certainty,
    eventStatus,
  ]);
  function clearDraft() {
    if (draftKey)
      try {
        localStorage.removeItem(draftKey);
      } catch {
        /* The server receipt remains authoritative. */
      }
  }
  const save = useMutation({
    mutationFn: () => {
      const body = {
        title: title.trim(),
        text,
        limitations,
        source_refs: refs,
        ...(selectedKind === "clue"
          ? {
              date: {
                text: dateText,
                normalized: dateNormalized || null,
                certainty,
              },
              event_status: eventStatus,
            }
          : {}),
      };
      return record
        ? studies.updateRecord(record.id, {
            expected_version: baseVersion,
            ...body,
          })
        : studies.createRecord(topic.id, { kind: selectedKind, ...body });
    },
    onSuccess: (value) => {
      clearDraft();
      onSaved(value);
    },
  });
  return (
    <form
      className="study-editor"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <div className="study-detail-heading">
        <h2>{record ? "编辑个人记录" : "新建个人记录"}</h2>
        <span className="study-badge">个人整理，不改变原文核验状态</span>
      </div>
      {record && <AdoptedAnalysisLink record={record} />}
      {retained && (
        <p role="status">
          已恢复本机编辑草稿。
          {record &&
            baseVersion !== record.version &&
            "服务端记录已有更新；保存时会检查原始版本，请先保留当前内容。"}
        </p>
      )}
      {draftError && <p role="alert">{draftError}</p>}
      {!record && (
        <label>
          记录类型
          <select
            aria-label="新记录类型"
            value={selectedKind}
            onChange={(event) => setKind(event.target.value as StudyRecordKind)}
          >
            {Object.entries(recordLabels).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        记录标题
        <input
          aria-label="记录标题"
          value={title}
          required
          maxLength={300}
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <label>
        正文（Markdown）
        <textarea
          aria-label="记录正文"
          value={text}
          maxLength={60000}
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      <Button
        type="button"
        variant="ghost"
        onClick={() => setPreview(!preview)}
      >
        {preview ? "收起正文预览" : "预览正文"}
      </Button>
      {preview && <RenderedMarkdown markdown={text} />}
      <label>
        使用限制与待核事项
        <textarea
          aria-label="使用限制"
          value={limitations}
          maxLength={10000}
          onChange={(event) => setLimitations(event.target.value)}
        />
      </label>
      <fieldset className="study-source-choices">
        <legend>关联原文依据</legend>
        {topic.materials.length ? (
          topic.materials.map((material) => (
            <label className="study-check" key={material.id}>
              <input
                type="checkbox"
                checked={refs.includes(material.id)}
                disabled={
                  !material.source_current && !refs.includes(material.id)
                }
                onChange={(event) =>
                  setRefs(
                    event.target.checked
                      ? [...refs, material.id]
                      : refs.filter((ref) => ref !== material.id),
                  )
                }
              />
              {material.title}
              {!material.source_current && " · 保留原引用，来源待核"}
            </label>
          ))
        ) : (
          <p>专题暂无选材，可先保存个人记录，再补充来源。</p>
        )}
      </fieldset>
      {selectedKind === "clue" && (
        <div className="study-clue-fields">
          <p>个人时间线索单独保存，不改写冻结史料的日期。</p>
          <label>
            原文或整理日期表述
            <input
              value={dateText}
              maxLength={2000}
              onChange={(event) => setDateText(event.target.value)}
            />
          </label>
          <label>
            排序日期（年、月、日或区间）
            <input
              aria-label="线索排序日期"
              placeholder="1948、1948-01 或 1948-01-10"
              value={dateNormalized}
              onChange={(event) => setDateNormalized(event.target.value)}
            />
          </label>
          <label>
            日期性质
            <select
              value={certainty}
              onChange={(event) =>
                setCertainty(event.target.value as typeof certainty)
              }
            >
              <option value="unknown">未定年</option>
              <option value="stated">明确记载</option>
              <option value="inferred">据材料推定</option>
              <option value="disputed">存在争议</option>
            </select>
          </label>
          <label>
            事项性质
            <select
              value={eventStatus}
              onChange={(event) => setStatus(event.target.value)}
            >
              <option value="unknown">尚未核对</option>
              <option value="reported">材料报告发生</option>
              <option value="planned">计划或拟议</option>
              <option value="conditional">附带条件</option>
              <option value="negated">原文否定发生</option>
            </select>
          </label>
          <EventNature value={{ event_status: eventStatus }} />
        </div>
      )}
      {save.isError && <p role="alert">{save.error.message}</p>}
      <div className="study-actions">
        <Button type="submit" disabled={save.isPending || !title.trim()}>
          {save.isPending ? "正在保存…" : "保存记录"}
        </Button>
        <Button
          type="button"
          variant="ghost"
          disabled={save.isPending}
          onClick={() => {
            clearDraft();
            onCancel();
          }}
        >
          取消编辑
        </Button>
      </div>
    </form>
  );
}

type RecordDraft = {
  expected_version: number;
  kind: StudyRecordKind;
  title: string;
  text: string;
  limitations: string;
  source_refs: string[];
  date_text: string;
  date_normalized: string;
  certainty: StudyDate["certainty"];
  event_status: string;
};
function readDraft(key: string): RecordDraft | null {
  if (!key) return null;
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    if (
      !value ||
      !Number.isSafeInteger(value.expected_version) ||
      value.expected_version < 0 ||
      !Object.hasOwn(recordLabels, value.kind)
    )
      return null;
    if (
      ![
        "title",
        "text",
        "limitations",
        "date_text",
        "date_normalized",
        "event_status",
      ].every((field) => typeof value[field] === "string")
    )
      return null;
    if (
      !["stated", "inferred", "disputed", "unknown"].includes(
        value.certainty,
      ) ||
      !Array.isArray(value.source_refs) ||
      !value.source_refs.every((ref: unknown) => typeof ref === "string")
    )
      return null;
    return value as RecordDraft;
  } catch {
    return null;
  }
}
