import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { RenderedMarkdown } from "@/editor/RenderedMarkdown";
import { studies, studyKeys } from "./api";
import {
  ChooseTopic,
  StudyErrorState,
  TopicChooser,
  useStudyTopic,
} from "./StudyTopicScope";
import { MaterialPreview } from "./StudyTopics";
import type { StudyAnalysis, StudyAnalysisSuggestion } from "./types";

const modeLabels = {
  summary: "材料摘要",
  compare: "记载对照",
  leads: "后续线索建议",
};
const stateLabels = {
  queued: "等待原生代理",
  running: "正在阅读与分析",
  verification: "正在独立核对依据",
  ready: "建议已就绪",
  failed: "分析未完成",
  cancelled: "已取消",
};
const activeStates = new Set(["queued", "running", "verification"]);

export function AnalysisPage() {
  const scope = useStudyTopic();
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("analysis") || "";
  const [mode, setMode] = useState<StudyAnalysis["mode"]>("summary");
  const [materials, setMaterials] = useState<string[]>([]);
  const [prompt, setPrompt] = useState("");
  const [source, setSource] = useState<{
    material_id: string;
    evidence_id: string;
  } | null>(null);
  const cache = useQueryClient();
  useEffect(() => {
    setMaterials([]);
    setSource(null);
  }, [scope.topicId]);
  const analyses = useQuery({
    queryKey: studyKeys.analyses(scope.topicId),
    queryFn: ({ signal }) => studies.analyses(scope.topicId, signal),
    enabled: !!scope.topicId,
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => activeStates.has(item.state))
        ? 3000
        : false,
  });
  const active = useQuery({
    queryKey: ["studies", "analysis", selectedId],
    queryFn: ({ signal }) => studies.analysis(selectedId, signal),
    enabled: !!selectedId,
    retry: false,
    refetchInterval: (query) =>
      query.state.data && activeStates.has(query.state.data.state)
        ? 2000
        : false,
  });
  function select(id: string) {
    const next = new URLSearchParams(params);
    next.set("analysis", id);
    setParams(next);
  }
  const create = useMutation({
    mutationFn: () =>
      studies.createAnalysis(scope.topicId, {
        mode,
        material_ids: materials,
        prompt,
      }),
    onSuccess: (analysis) => {
      cache.setQueryData(["studies", "analysis", analysis.id], analysis);
      void cache.invalidateQueries({
        queryKey: studyKeys.analyses(scope.topicId),
      });
      select(analysis.id);
    },
  });
  const cancel = useMutation({
    mutationFn: (analysis: StudyAnalysis) =>
      studies.cancelAnalysis(analysis.id, analysis.version),
    onSuccess: (analysis) => {
      cache.setQueryData(["studies", "analysis", analysis.id], analysis);
      void cache.invalidateQueries({
        queryKey: studyKeys.analyses(scope.topicId),
      });
    },
  });
  const running = analyses.data?.items.some((item) =>
    activeStates.has(item.state),
  );
  return (
    <section className="study-page">
      <header className="study-page-heading">
        <div>
          <p className="study-eyebrow">专题研究</p>
          <h1>AI 分析</h1>
          <p>对照所选材料，沿原文判断建议是否可用。</p>
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
        <p role="status">正在读取专题材料…</p>
      ) : (
        <div className="study-analysis-layout">
          <aside>
            <form
              className="study-editor"
              onSubmit={(event) => {
                event.preventDefault();
                create.mutate();
              }}
            >
              <h2>选择分析范围</h2>
              <label>
                分析方式
                <select
                  aria-label="分析方式"
                  value={mode}
                  onChange={(event) =>
                    setMode(event.target.value as StudyAnalysis["mode"])
                  }
                >
                  {Object.entries(modeLabels).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <fieldset className="study-source-choices">
                <legend>使用的材料 · 已选 {materials.length} / 12 项</legend>
                {scope.topic.data.materials.map((material) => (
                  <label key={material.id} className="study-check">
                    <input
                      type="checkbox"
                      checked={materials.includes(material.id)}
                      disabled={
                        !material.source_current ||
                        (!materials.includes(material.id) &&
                          materials.length >= 12)
                      }
                      onChange={(event) =>
                        setMaterials(
                          event.target.checked
                            ? [...materials, material.id]
                            : materials.filter((id) => id !== material.id),
                        )
                      }
                    />
                    {material.title}
                    {!material.source_current && " · 来源待核"}
                  </label>
                ))}
                {!scope.topic.data.materials.length && (
                  <p>先在专题中添加已核验史料卡。</p>
                )}
              </fieldset>
              <label>
                关注的问题或比较角度
                <textarea
                  aria-label="分析关注点"
                  value={prompt}
                  maxLength={6000}
                  onChange={(event) => setPrompt(event.target.value)}
                  placeholder="例如：比较几份材料对同一措施的记载差异。"
                />
              </label>
              <p className="study-muted">
                建议与引用会单独核对。采纳后成为个人研究记录，不授予原文内容审批。
              </p>
              {create.isError && <p role="alert">{create.error.message}</p>}
              <Button
                type="submit"
                disabled={
                  !materials.length ||
                  create.isPending ||
                  scope.topic.data.archived
                }
              >
                {create.isPending ? "正在提交…" : "分析所选材料"}
              </Button>
              {running && (
                <p role="status">
                  已有分析任务进行中，可继续阅读和整理其他记录。
                </p>
              )}
            </form>
            <div className="study-analysis-history">
              <h3>分析记录</h3>
              {analyses.isError ? (
                <StudyErrorState
                  error={analyses.error}
                  retry={() => void analyses.refetch()}
                />
              ) : analyses.data?.items.length ? (
                analyses.data.items.map((analysis) => (
                  <button
                    key={analysis.id}
                    className="study-topic-item"
                    aria-pressed={selectedId === analysis.id}
                    onClick={() => select(analysis.id)}
                  >
                    <strong>{modeLabels[analysis.mode]}</strong>
                    <span>{stateLabels[analysis.state]}</span>
                  </button>
                ))
              ) : (
                <p>尚未提交分析。</p>
              )}
            </div>
          </aside>
          <div className="study-analysis-results">
            {!selectedId ? (
              <div className="study-empty">
                <h2>建议与依据显示在这里</h2>
                <p>从材料选择开始，也可以打开已有分析记录。</p>
              </div>
            ) : active.isError ? (
              <StudyErrorState
                error={active.error}
                retry={() => void active.refetch()}
              />
            ) : !active.data ? (
              <p role="status">正在读取分析记录…</p>
            ) : (
              <>
                <div className="study-detail-heading">
                  <div>
                    <h2>{modeLabels[active.data.mode]}</h2>
                    <p role="status">
                      {stateLabels[active.data.state]} · 材料{" "}
                      {active.data.material_ids.length} 项
                    </p>
                  </div>
                  {activeStates.has(active.data.state) && (
                    <Button
                      variant="outline"
                      disabled={cancel.isPending}
                      onClick={() => active.data && cancel.mutate(active.data)}
                    >
                      取消分析
                    </Button>
                  )}
                </div>
                {cancel.isError && <p role="alert">{cancel.error.message}</p>}
                {active.data.error && <p role="alert">{active.data.error}</p>}
                {activeStates.has(active.data.state) && (
                  <p className="study-chronology-notice">
                    原生代理正在按冻结来源处理；任务保存进度，离开本页不会代替你采纳建议。
                  </p>
                )}
                {active.data.state === "ready" &&
                  !active.data.suggestions.length && (
                    <p>本次没有形成可采用的建议，请查阅任务说明和所选材料。</p>
                  )}
                {active.data.suggestions.map((suggestion) => (
                  <Suggestion
                    key={suggestion.id}
                    suggestion={suggestion}
                    analysis={active.data!}
                    busy={active.isFetching}
                    onSource={setSource}
                    onAdopted={() => {
                      void active.refetch();
                      void cache.invalidateQueries({
                        queryKey: studyKeys.records(scope.topicId),
                      });
                    }}
                  />
                ))}
              </>
            )}
            {source && (
              <MaterialPreview
                topicId={scope.topicId}
                materialId={source.material_id}
                evidenceId={source.evidence_id}
                onClose={() => setSource(null)}
              />
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function Suggestion({
  suggestion,
  analysis,
  busy,
  onSource,
  onAdopted,
}: {
  suggestion: StudyAnalysisSuggestion;
  analysis: StudyAnalysis;
  busy: boolean;
  onSource: (ref: { material_id: string; evidence_id: string }) => void;
  onAdopted: () => void;
}) {
  const [kind, setKind] = useState<"note" | "argument" | "thought">("note");
  const [adopted, setSaved] = useState(false);
  const saved = adopted || Boolean(suggestion.adopted_record_id);
  const adopt = useMutation({
    mutationFn: () =>
      studies.adoptSuggestion(analysis.id, {
        expected_version: analysis.version,
        suggestion_id: suggestion.id,
        kind,
        title: suggestion.title,
      }),
    onSuccess: () => {
      setSaved(true);
      onAdopted();
    },
  });
  const usable =
    analysis.state === "ready" &&
    suggestion.verified &&
    analysis.source_current !== false;
  const label =
    kind === "note" ? "札记" : kind === "argument" ? "论据" : "观点";
  return (
    <article className="study-analysis-suggestion">
      <div className="study-detail-heading">
        <span className="study-badge">
          AI 建议 · {suggestion.verified ? "引用已核对" : "引用待核对"}
        </span>
        {saved && <span role="status">已采纳为个人记录</span>}
      </div>
      <h3>{suggestion.title}</h3>
      <RenderedMarkdown markdown={suggestion.text} />
      {suggestion.limitations && (
        <div className="study-limitations">
          <strong>使用限制</strong>
          <p className="study-preserve">{suggestion.limitations}</p>
        </div>
      )}
      <div className="study-source-links">
        {suggestion.evidence_refs.map((ref, index) => (
          <button
            key={`${ref.material_id}:${ref.evidence_id}`}
            onClick={() => onSource(ref)}
          >
            查看原文依据 {index + 1} · {ref.evidence_id}
          </button>
        ))}
      </div>
      {!!suggestion.issues?.length && (
        <div className="study-limitations">
          <strong>核对说明</strong>
          <ul>
            {suggestion.issues.map((issue, index) => (
              <li key={index}>{issue}</li>
            ))}
          </ul>
        </div>
      )}
      {!usable && !saved && (
        <p className="study-muted">
          建议尚未通过引用核对或来源已变化，目前保留供查阅。
        </p>
      )}
      <div className="study-actions">
        <label>
          采纳为
          <select
            aria-label={`采纳类型 · ${suggestion.title}`}
            value={kind}
            disabled={saved || adopt.isPending}
            onChange={(event) => setKind(event.target.value as typeof kind)}
          >
            <option value="note">札记</option>
            <option value="argument">论据</option>
            <option value="thought">观点</option>
          </select>
        </label>
        <Button
          disabled={!usable || busy || saved || adopt.isPending}
          onClick={() => adopt.mutate()}
        >
          {adopt.isPending ? "正在采纳…" : saved ? "已采纳" : `采纳为${label}`}
        </Button>
        {saved && (
          <Link
            to={`/arguments?topic=${encodeURIComponent(analysis.topic_id)}&kind=${kind}`}
          >
            查看个人记录
          </Link>
        )}
      </div>
      {adopt.isError && <p role="alert">{adopt.error.message}</p>}
    </article>
  );
}
