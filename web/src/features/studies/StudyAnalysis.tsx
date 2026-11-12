import { useEffect, useState } from "react";
import {
  skipToken,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import AnalysisSuggestion from "./AnalysisSuggestion";
import AnalysisPreparation, {
  analysisModeLabels as modeLabels,
} from "./AnalysisPreparation";
import { studies, studyKeys } from "./api";
import {
  ChooseTopic,
  StudyErrorState,
  TopicChooser,
  useStudyTopic,
} from "./StudyTopicScope";
import { MaterialPreview } from "./StudyTopics";
import type { StudyAnalysis } from "./types";

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
  const [preparing, setPreparing] = useState(!selectedId);
  const cache = useQueryClient();
  const runtime = useQuery<{ workspace_id?: string }>({
    queryKey: ["platform", "runtime"],
    queryFn: skipToken,
  });
  const workspace = runtime.data?.workspace_id || "";
  useEffect(() => setPreparing(!selectedId), [selectedId]);
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
    enabled: !!selectedId && !!scope.topicId,
    retry: false,
    refetchInterval: (query) =>
      query.state.data && activeStates.has(query.state.data.state)
        ? 2000
        : false,
  });
  function change(changes: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes))
      value ? next.set(key, value) : next.delete(key);
    setParams(next);
  }
  function select(id: string) {
    setPreparing(false);
    change({
      analysis: id,
      showIgnored: "",
      suggestion: "",
      evidence: "",
      proof_material: "",
      proof: "",
    });
  }
  function selectSuggestion(id: string) {
    change({
      suggestion: id,
      showIgnored: showIgnored ? "true" : "",
      evidence: "",
      proof_material: "",
      proof: "",
    });
  }
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
  const analysis =
    active.data?.topic_id === scope.topicId ? active.data : undefined;
  const requestedSuggestionId = params.get("suggestion");
  const showIgnored =
    params.get("showIgnored") === "true" ||
    !!analysis?.suggestions.find((item) => item.id === requestedSuggestionId)
      ?.ignored;
  const suggestions =
    analysis?.suggestions.filter((item) => showIgnored || !item.ignored) || [];
  const suggestion = requestedSuggestionId
    ? suggestions.find((item) => item.id === requestedSuggestionId)
    : suggestions[0];
  const currentMaterialIds = new Set(
    scope.topic.data?.materials
      .filter((item) => item.source_current)
      .map((item) => item.id),
  );
  const sourceCurrent =
    !!suggestion?.evidence_refs.length &&
    suggestion.evidence_refs.every((ref) =>
      currentMaterialIds.has(ref.material_id),
    );
  const requestedProof = params.has("proof_material") || params.has("evidence");
  const source =
    params.get("proof") === "closed"
      ? undefined
      : requestedProof
        ? suggestion?.evidence_refs.find(
            (ref) =>
              ref.material_id === params.get("proof_material") &&
              ref.evidence_id === params.get("evidence"),
          )
        : suggestion?.evidence_refs[0];
  const ignoredCount =
    analysis?.suggestions.filter((item) => item.ignored).length || 0;
  const stage = preparing ? 0 : analysis?.state === "ready" ? 2 : 1;
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
      ) : scope.topic.isError && !scope.topic.data ? (
        <StudyErrorState
          error={scope.topic.error}
          retry={() => void scope.topic.refetch()}
        />
      ) : !scope.topic.data ? (
        <p role="status">正在读取专题材料…</p>
      ) : (
        <>
          <ol className="study-analysis-stages" aria-label="分析阶段">
            {["准备材料", "制作与独立核验", "查看建议与采纳"].map(
              (label, index) => (
                <li
                  key={label}
                  aria-current={stage === index ? "step" : undefined}
                >
                  <span>{index + 1}</span>
                  {label}
                </li>
              ),
            )}
          </ol>
          <div className="study-analysis-layout" data-preparing={preparing}>
            <aside className="study-analysis-index">
              <Button variant="outline" onClick={() => setPreparing(true)}>
                新建分析
              </Button>
              <details
                className="study-analysis-history"
                open={!selectedId || preparing}
              >
                <summary>分析记录 · {analyses.data?.total || 0}</summary>
                {analyses.isError ? (
                  <StudyErrorState
                    error={analyses.error}
                    retry={() => void analyses.refetch()}
                  />
                ) : analyses.data?.items.length ? (
                  analyses.data.items.map((item) => (
                    <button
                      key={item.id}
                      className="study-topic-item"
                      aria-pressed={selectedId === item.id && !preparing}
                      onClick={() => select(item.id)}
                    >
                      <strong>{modeLabels[item.mode]}</strong>
                      <span>{stateLabels[item.state]}</span>
                    </button>
                  ))
                ) : (
                  <p>尚未提交分析。</p>
                )}
              </details>
              {!preparing && analysis?.state === "ready" && (
                <>
                  <nav
                    className="study-suggestion-list"
                    aria-label="分析建议清单"
                  >
                    {suggestions.map((item, index) => (
                      <button
                        key={item.id}
                        className="study-topic-item"
                        aria-pressed={item.id === suggestion?.id}
                        onClick={() => selectSuggestion(item.id)}
                      >
                        <small>
                          {index + 1} ·{" "}
                          {item.adopted_record_id
                            ? "已采纳"
                            : item.ignored
                              ? "已忽略"
                              : item.verified
                                ? "引用已核对"
                                : "引用待核对"}
                        </small>
                        <strong>{item.title}</strong>
                      </button>
                    ))}
                  </nav>
                  {!!ignoredCount && (
                    <label className="study-check">
                      <input
                        type="checkbox"
                        checked={showIgnored}
                        onChange={(event) =>
                          change({
                            showIgnored: event.target.checked ? "true" : "",
                            ...(!event.target.checked && suggestion?.ignored
                              ? {
                                  suggestion: "",
                                  evidence: "",
                                  proof_material: "",
                                  proof: "",
                                }
                              : {}),
                          })
                        }
                      />
                      查看已忽略建议 · {ignoredCount}
                    </label>
                  )}
                </>
              )}
            </aside>
            <section className="study-analysis-results" aria-label="分析工作区">
              {preparing && selectedId && (
                <Button variant="ghost" onClick={() => setPreparing(false)}>
                  返回当前分析
                </Button>
              )}
              <AnalysisPreparation
                key={scope.topicId}
                topic={scope.topic.data}
                running={!!running}
                visible={preparing}
                onCreated={(analysis) => {
                  cache.setQueryData(
                    ["studies", "analysis", analysis.id],
                    analysis,
                  );
                  void cache.invalidateQueries({
                    queryKey: studyKeys.analyses(scope.topicId),
                  });
                  select(analysis.id);
                }}
              />
              {!preparing &&
                (active.isError && !active.data ? (
                  <StudyErrorState
                    error={active.error}
                    retry={() => void active.refetch()}
                  />
                ) : !active.data ? (
                  <p role="status">正在读取分析记录…</p>
                ) : !analysis ? (
                  <p role="alert">
                    该分析不属于当前专题，请从分析记录重新选择。
                  </p>
                ) : (
                  <>
                    <div className="study-detail-heading">
                      <div>
                        <h2>{modeLabels[analysis.mode]}</h2>
                        <p role="status">
                          {stateLabels[analysis.state]} · 材料{" "}
                          {analysis.material_ids.length} 项
                        </p>
                      </div>
                      {activeStates.has(analysis.state) && (
                        <Button
                          variant="outline"
                          disabled={cancel.isPending}
                          onClick={() => cancel.mutate(analysis)}
                        >
                          取消分析
                        </Button>
                      )}
                    </div>
                    {cancel.isError && (
                      <p role="alert">{cancel.error.message}</p>
                    )}
                    {analysis.error && <p role="alert">{analysis.error}</p>}
                    {activeStates.has(analysis.state) && (
                      <p className="study-chronology-notice">
                        原生代理正在按冻结来源处理；任务保存进度，可离开本页继续阅读。核验结果不会自动成为你的研究记录。
                      </p>
                    )}
                    {analysis.state === "ready" && !suggestion && (
                      <div className="study-empty">
                        <h3>
                          {requestedSuggestionId
                            ? "未找到所选建议，请从清单重新选择"
                            : analysis.suggestions.length
                              ? "当前建议已全部处理"
                              : "本次没有形成可采用的建议"}
                        </h3>
                        <p>已忽略的建议仍保留，可在清单中重新显示。</p>
                      </div>
                    )}
                    {suggestion && (
                      <div className="study-analysis-review">
                        <AnalysisSuggestion
                          key={`${workspace}:${analysis.id}:${suggestion.id}`}
                          workspace={workspace}
                          suggestion={suggestion}
                          analysis={analysis}
                          sourceCurrent={sourceCurrent}
                          busy={active.isFetching}
                          onSource={(ref) =>
                            change({
                              proof_material: ref.material_id,
                              evidence: ref.evidence_id,
                              proof: "",
                            })
                          }
                          onAdopted={() => {
                            void active.refetch();
                            void cache.invalidateQueries({
                              queryKey: studyKeys.records(scope.topicId),
                            });
                          }}
                          onReload={async () => {
                            change({
                              suggestion: suggestion.id,
                              showIgnored: showIgnored ? "true" : "",
                            });
                            const [latest, topic] = await Promise.all([
                              active.refetch(),
                              scope.topic.refetch(),
                            ]);
                            return !latest.isError && !topic.isError;
                          }}
                          onDisposition={(saved) => {
                            cache.setQueryData(
                              ["studies", "analysis", saved.id],
                              saved,
                            );
                            void cache.invalidateQueries({
                              queryKey: studyKeys.analyses(scope.topicId),
                            });
                            if (!showIgnored)
                              selectSuggestion(
                                saved.suggestions.find(
                                  (item) =>
                                    !item.ignored && item.id !== suggestion.id,
                                )?.id || "",
                              );
                          }}
                        />
                        <aside
                          className="study-analysis-proof"
                          aria-label="所选建议的原文依据"
                        >
                          {source ? (
                            <MaterialPreview
                              key={`${analysis.id}:${suggestion.id}:${source.material_id}:${source.evidence_id}`}
                              topicId={scope.topicId}
                              materialId={source.material_id}
                              evidenceId={source.evidence_id}
                              onClose={() => change({ proof: "closed" })}
                            />
                          ) : (
                            <div className="study-empty">
                              <p>
                                {requestedProof &&
                                params.get("proof") !== "closed"
                                  ? "指定的原文依据不属于当前建议，请重新选择依据。"
                                  : "从当前建议中选择原文依据，在此对照。"}
                              </p>
                            </div>
                          )}
                        </aside>
                      </div>
                    )}
                  </>
                ))}
            </section>
          </div>
        </>
      )}
    </section>
  );
}
