import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import EventNature from "@/features/research/EventNature";
import NetworkEvidence from "@/features/research/NetworkEvidence";
import { entityWorkspaceHref } from "@/features/research/network-query";
import { studies } from "./api";
import {
  ChooseTopic,
  StudyErrorState,
  TopicChooser,
  useStudyTopic,
} from "./StudyTopicScope";
import { RecordEditor } from "./StudyRecords";
import { yearReference } from "./era-helper";
import type { StudyRecord, StudyTimelineItem } from "./types";

export function TopicTimelinePage() {
  const scope = useStudyTopic();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const basis =
    params.get("date_basis") === "formation" ? "formation" : "event";
  const [draftBasis, setBasis] = useState(basis);
  const offset = Math.max(
    0,
    Math.min(2_000_000_000, Number(params.get("offset")) || 0),
  );
  const [start, setStart] = useState(params.get("date_from") || "");
  const [end, setEnd] = useState(params.get("date_to") || "");
  const [includeUndated, setUndated] = useState(
    params.get("undated") !== "false",
  );
  const [material, setMaterial] = useState(params.get("material_id") || "");
  const [editing, setEditing] = useState<StudyRecord | "new" | null>(null);
  useEffect(() => {
    setEditing(null);
  }, [scope.topicId]);
  const selectedId = params.get("selected") || "";
  const filters = {
    date_basis: basis,
    date_from: params.get("date_from") || "",
    date_to: params.get("date_to") || "",
    include_undated: params.get("undated") !== "false",
    material_id: params.get("material_id") || "",
    offset,
    limit: 20,
  } as const;
  useEffect(() => {
    setBasis(basis);
    setStart(filters.date_from);
    setEnd(filters.date_to);
    setUndated(filters.include_undated);
    setMaterial(filters.material_id);
  }, [
    scope.topicId,
    basis,
    filters.date_from,
    filters.date_to,
    filters.include_undated,
    filters.material_id,
  ]);
  const filtersChanged =
    draftBasis !== basis ||
    start !== filters.date_from ||
    end !== filters.date_to ||
    includeUndated !== filters.include_undated ||
    material !== filters.material_id;
  const cache = useQueryClient();
  const timeline = useQuery({
    queryKey: ["studies", "timeline", scope.topicId, filters],
    queryFn: ({ signal }) => studies.timeline(scope.topicId, filters, signal),
    enabled: !!scope.topicId,
    retry: false,
    staleTime: 30_000,
  });
  function change(changes: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes))
      value ? next.set(key, value) : next.delete(key);
    setParams(next);
  }
  const item = timeline.data?.items.find((item) => item.id === selectedId);
  return (
    <section className="study-page">
      <header className="study-page-heading">
        <div>
          <p className="study-eyebrow">专题研究</p>
          <h1>时间线索</h1>
          <p>沿专题材料中的日期阅读，未定年和个人补记各自保留。</p>
        </div>
        <div className="study-actions">
          <TopicChooser scope={scope} />
          <Button
            disabled={!scope.topic.data || scope.topic.data.archived}
            onClick={() => setEditing("new")}
          >
            添加个人线索
          </Button>
        </div>
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
          {editing && (
            <RecordEditor
              key={editing === "new" ? scope.topicId : editing.id}
              topic={scope.topic.data}
              record={editing === "new" ? undefined : editing}
              kind="clue"
              onSaved={() => {
                setEditing(null);
                void cache.invalidateQueries({
                  queryKey: ["studies", "timeline", scope.topicId],
                });
              }}
              onCancel={() => setEditing(null)}
            />
          )}
          <form
            className="study-timeline-filters"
            onSubmit={(event) => {
              event.preventDefault();
              change({
                date_basis: draftBasis,
                date_from: start,
                date_to: end,
                undated: String(includeUndated),
                material_id: material,
                offset: "",
                selected: "",
              });
            }}
          >
            <label>
              日期依据
              <select
                aria-label="日期依据"
                value={draftBasis}
                onChange={(event) =>
                  setBasis(
                    event.target.value === "formation" ? "formation" : "event",
                  )
                }
              >
                <option value="event">材料叙述的事项日期</option>
                <option value="formation">文献形成日期</option>
              </select>
            </label>
            <label>
              起始时间
              <input
                aria-label="起始时间"
                placeholder="1948 或 1948-01"
                value={start}
                onChange={(event) => setStart(event.target.value)}
              />
            </label>
            <label>
              结束时间
              <input
                aria-label="结束时间"
                placeholder="1949 或 1949-12"
                value={end}
                onChange={(event) => setEnd(event.target.value)}
              />
            </label>
            <label>
              来源材料
              <select
                aria-label="时间线来源材料"
                value={material}
                onChange={(event) => setMaterial(event.target.value)}
              >
                <option value="">全部专题材料</option>
                {scope.topic.data.materials.map((material) => (
                  <option key={material.id} value={material.id}>
                    {material.title}
                  </option>
                ))}
              </select>
            </label>
            <label className="study-check">
              <input
                type="checkbox"
                checked={includeUndated}
                onChange={(event) => setUndated(event.target.checked)}
              />
              保留未定年
            </label>
            <Button type="submit" variant="outline">
              应用时间筛选
            </Button>
            {filtersChanged && <span role="status">筛选条件尚未应用</span>}
          </form>
          <p className="study-filter-status">
            当前生效：
            {basis === "formation" ? "文献形成日期" : "材料叙述的事项日期"}
            {filters.date_from && ` · 从 ${filters.date_from}`}
            {filters.date_to && ` · 至 ${filters.date_to}`}
            {filters.material_id
              ? ` · ${scope.topic.data.materials.find((material) => material.id === filters.material_id)?.title || "选定材料"}`
              : " · 全部专题材料"}
            {filters.include_undated ? " · 包含未定年" : " · 仅已定年"}
          </p>
          {basis === "formation" && (
            <p className="study-chronology-notice">
              文献形成日期不代表材料叙述的事项发生日期。
            </p>
          )}
          <div className="study-timeline-layout">
            <div>
              {timeline.isError ? (
                <StudyErrorState
                  error={timeline.error}
                  retry={() => void timeline.refetch()}
                />
              ) : timeline.isPending ? (
                <p role="status">正在排列时间线索…</p>
              ) : (
                <>
                  <div className="study-detail-heading">
                    <div>
                      <h2>{timeline.data.total} 条线索</h2>
                      <p>
                        已定年 {timeline.data.dated_total} 条 · 未定年{" "}
                        {timeline.data.undated_total} 条
                      </p>
                    </div>
                    {timeline.data.undated_total > 0 &&
                      timeline.data.dated_total > 0 && (
                        <Button
                          variant="ghost"
                          onClick={() =>
                            change({
                              offset: String(timeline.data.dated_total),
                              selected: "",
                            })
                          }
                        >
                          定位未定年线索
                        </Button>
                      )}
                  </div>
                  {!timeline.data.items.length ? (
                    <p className="study-empty">
                      当前范围没有线索。可调整日期或保留未定年记录。
                    </p>
                  ) : (
                    <ol className="study-timeline">
                      {timeline.data.items.map((entry) => (
                        <li
                          key={entry.id}
                          className="study-timeline-entry"
                          data-selected={selectedId === entry.id}
                        >
                          <div className="study-timeline-date">
                            <strong>
                              {entry.undated
                                ? "未定年"
                                : entry.date_start ||
                                  entry.date?.text ||
                                  "日期未明"}
                            </strong>
                            <small>
                              {entry.item_kind === "personal"
                                ? "个人补记"
                                : basis === "formation"
                                  ? "文献形成"
                                  : "来源记载"}
                            </small>
                          </div>
                          <article>
                            <div className="study-detail-heading">
                              <button
                                className="study-text-button"
                                aria-pressed={selectedId === entry.id}
                                onClick={() => change({ selected: entry.id })}
                              >
                                {entry.name}
                              </button>
                              <span className="study-badge">
                                {entry.item_kind === "personal"
                                  ? "个人线索"
                                  : "史料记录"}
                              </span>
                            </div>
                            {entry.date?.text && (
                              <p className="study-date-original">
                                {entry.date.text}
                                {entry.date.certainty === "inferred" &&
                                  " · 据材料推定"}
                                {entry.date.certainty === "disputed" &&
                                  " · 日期有争议"}
                              </p>
                            )}
                            {(entry.item_kind === "event" ||
                              (entry.item_kind === "personal" &&
                                entry.event_status &&
                                entry.event_status !== "unknown")) && (
                              <EventNature value={entry} />
                            )}
                            <p>
                              {entry.description.length > 300
                                ? `${entry.description.slice(0, 300)}…`
                                : entry.description}
                            </p>
                            <Button
                              variant="ghost"
                              onClick={() => change({ selected: entry.id })}
                            >
                              侧边查看依据
                            </Button>
                          </article>
                        </li>
                      ))}
                    </ol>
                  )}
                  <nav
                    className="study-actions study-timeline-pagination"
                    aria-label="线索分页"
                  >
                    <Button
                      variant="outline"
                      disabled={!offset || timeline.isFetching}
                      onClick={() =>
                        change({
                          offset: String(Math.max(0, offset - 20)),
                          selected: "",
                        })
                      }
                    >
                      上一页线索
                    </Button>
                    <span>
                      {timeline.data.total ? offset + 1 : 0}–
                      {Math.min(offset + 20, timeline.data.total)} /{" "}
                      {timeline.data.total}
                    </span>
                    <Button
                      variant="outline"
                      disabled={
                        offset + 20 >= timeline.data.total ||
                        timeline.isFetching
                      }
                      onClick={() =>
                        change({ offset: String(offset + 20), selected: "" })
                      }
                    >
                      下一页线索
                    </Button>
                  </nav>
                </>
              )}
            </div>
            <aside className="study-timeline-side">
              {item && (
                <div className="study-timeline-proof" key={item.id}>
                  {item.item_kind === "personal" ? (
                    <PersonalClue
                      item={item}
                      topicId={scope.topicId}
                      onEdit={setEditing}
                    />
                  ) : (
                    <NetworkEvidence
                      selection={{
                        kind:
                          item.item_kind === "document" ? "document" : "event",
                        id: item.id,
                      }}
                      item={item}
                      bookId={item.book_id}
                      onCenter={(id) =>
                        navigate(entityWorkspaceHref(id, item.book_id))
                      }
                      onTimeline={(id) =>
                        navigate(
                          entityWorkspaceHref(id, item.book_id, "timeline"),
                        )
                      }
                      onClear={() => change({ selected: "" })}
                      onReload={() => void timeline.refetch()}
                    />
                  )}
                </div>
              )}
              {!item && (
                <div className="study-timeline-proof study-empty">
                  <h2>查看当前线索依据</h2>
                  <p>选择左侧线索，在此对照来源与日期表述。</p>
                </div>
              )}
              <details className="study-era-disclosure">
                <summary>纪年查阅工具</summary>
                <EraHelper />
              </details>
            </aside>
          </div>
        </>
      )}
    </section>
  );
}

function PersonalClue({
  item,
  onEdit,
}: {
  item: Extract<StudyTimelineItem, { item_kind: "personal" }>;
  topicId: string;
  onEdit: (record: StudyRecord) => void;
}) {
  const record = useQuery({
    queryKey: ["studies", "record", item.personal_record_id],
    queryFn: ({ signal }) => studies.record(item.personal_record_id, signal),
    retry: false,
  });
  return (
    <>
      <span className="study-badge">个人补记</span>
      <h2>{item.name}</h2>
      <p className="study-preserve">{item.description}</p>
      <p>{item.status_note}</p>
      {record.isError ? (
        <StudyErrorState
          error={record.error}
          retry={() => void record.refetch()}
        />
      ) : (
        <Button
          variant="outline"
          disabled={!record.data}
          onClick={() => record.data && onEdit(record.data)}
        >
          编辑个人线索
        </Button>
      )}
    </>
  );
}

function EraHelper() {
  const [year, setYear] = useState("");
  const [result, setResult] = useState<ReturnType<typeof yearReference>>();
  const [error, setError] = useState("");
  return (
    <section className="study-era-helper">
      <p className="study-eyebrow">纪年查阅</p>
      <h2>公元年与常用纪年</h2>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          try {
            setResult(yearReference(Number(year)));
            setError("");
          } catch (value) {
            setResult(undefined);
            setError(value instanceof Error ? value.message : "请检查年份。");
          }
        }}
      >
        <label>
          公元年
          <input
            aria-label="换算公元年"
            inputMode="numeric"
            value={year}
            onChange={(event) => setYear(event.target.value)}
            placeholder="例如 1875"
          />
        </label>
        <Button type="submit" variant="outline">
          查阅纪年
        </Button>
      </form>
      {error && <p role="alert">{error}</p>}
      {result && (
        <div role="status">
          <p>年干支标记：{result.ganzhi}</p>
          {result.eras.length ? (
            <ul>
              {result.eras.map((era) => (
                <li key={era}>{era}</li>
              ))}
            </ul>
          ) : (
            <p>此年份未收录常用年号对照。</p>
          )}
          <p className="study-era-note">{result.caution}</p>
          <a
            href="https://www.ris.gov.tw/documents/html/8/1/219.html"
            target="_blank"
            rel="noreferrer"
          >
            年岁对照表
          </a>
        </div>
      )}
    </section>
  );
}
