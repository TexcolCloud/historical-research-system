import { useEffect, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@/client/schema";
import { Button } from "@/components/ui/button";
import { readingHref } from "@/features/library/reader-location";
import { studies, studyKeys, studyRequest } from "./api";

type Discovery = components["schemas"]["StudySearchResponse"];

export function GlobalSearchPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const [text, setText] = useState(params.get("q") ?? "");
  const [scope, setScope] = useState(params.get("topic") ?? "");
  const [semantic, setSemantic] = useState(params.get("semantic") !== "false");
  const topics = useQuery({
    queryKey: studyKeys.topics(),
    queryFn: ({ signal }) => studies.topics(false, signal),
  });
  const q = params.get("q") ?? "",
    topic = params.get("topic") ?? "";
  const targetTopic = params.has("addTo") ? (params.get("addTo") ?? "") : topic;
  const useSemantic = params.get("semantic") !== "false";
  useEffect(() => {
    setText(q);
    setScope(topic);
    setSemantic(useSemantic);
  }, [q, topic, useSemantic]);
  const results = useQuery({
    queryKey: ["studies", "full-search", q, topic, params.get("semantic")],
    enabled: !!q,
    retry: false,
    queryFn: ({ signal }) =>
      studyRequest<Discovery>(
        `/search?${new URLSearchParams({ q, ...(topic ? { topic_id: topic } : {}), semantic: String(params.get("semantic") !== "false") })}`,
        "GET",
        undefined,
        signal,
      ),
  });
  const selectedTopic = useQuery({
    queryKey: studyKeys.topic(targetTopic),
    enabled: !!targetTopic,
    queryFn: ({ signal }) => studies.topic(targetTopic, signal),
  });
  const cache = useQueryClient();
  const add = useMutation({
    mutationFn: (card: { id: string; version: number }) => {
      if (!selectedTopic.data || selectedTopic.data.archived)
        throw new Error("请选择一个尚未归档的研究专题。");
      return studies.addMaterial(targetTopic, {
        expected_version: selectedTopic.data.version,
        card_id: card.id,
        card_version: card.version,
      });
    },
    onSuccess: (value) => {
      cache.setQueryData(studyKeys.topic(value.id), value);
    },
  });
  return (
    <section className="study-page">
      <header className="study-page-heading">
        <div>
          <h1>全文检索</h1>
          <p>在当前工作区的已发布原文中查找，或限定到专题选材。</p>
        </div>
      </header>
      <form
        className="study-search-form"
        onSubmit={(event) => {
          event.preventDefault();
          setParams(
            new URLSearchParams({
              q: text.trim(),
              ...(scope ? { topic: scope } : {}),
              ...(params.has("addTo")
                ? { addTo: params.get("addTo") ?? "" }
                : {}),
              semantic: String(semantic),
            }),
          );
        }}
      >
        <label>
          关键词或研究线索
          <input
            aria-label="全文检索关键词"
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="人名、机构、原文词语或问题"
            required
            maxLength={1000}
          />
        </label>
        <label>
          检索范围
          <select
            value={scope}
            onChange={(event) => setScope(event.target.value)}
          >
            <option value="">全库已发布资料</option>
            {scope &&
              !topics.data?.items.some((value) => value.id === scope) && (
                <option value={scope}>当前检索专题</option>
              )}
            {topics.data?.items.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title}
              </option>
            ))}
          </select>
        </label>
        <label className="study-checkbox">
          <input
            type="checkbox"
            checked={semantic}
            onChange={(event) => setSemantic(event.target.checked)}
          />
          语义与关键词综合检索
        </label>
        <Button type="submit" disabled={!text.trim() || results.isFetching}>
          检索原文
        </Button>
      </form>
      {results.isFetching && <p role="status">正在检索当前范围…</p>}
      {results.isError && (
        <p role="alert">
          {results.error.message}
          <Button onClick={() => void results.refetch()}>重试检索</Button>
        </p>
      )}
      {topics.isError && (
        <p role="alert">
          专题列表暂时无法读取。
          <Button onClick={() => void topics.refetch()}>重新读取专题</Button>
        </p>
      )}
      {selectedTopic.isError && (
        <p role="alert">
          {selectedTopic.error.message}
          <Button onClick={() => void selectedTopic.refetch()}>
            重新读取选材专题
          </Button>
        </p>
      )}
      {add.isError && (
        <p role="alert">
          {add.error.message}
          <Button onClick={() => void selectedTopic.refetch()}>
            重新读取选材专题
          </Button>
        </p>
      )}
      {add.isSuccess && <p role="status">已加入「{add.data.title}」。</p>}
      {results.data && !results.data.available && (
        <p role="alert">
          检索服务暂不可用，当前结果不能作为完整检索结论。
          <Button onClick={() => void results.refetch()}>重试检索</Button>
        </p>
      )}
      {results.data && (
        <>
          <p className="study-eyebrow">
            {topic
              ? topics.data?.items.find((t) => t.id === topic)?.title ||
                "所选专题"
              : "全库"}{" "}
            · {results.data.hits.length} 条原文结果 ·{" "}
            {results.data.backend === "hybrid" ? "综合检索" : "关键词检索"}
          </p>
          <label className="study-selection-target">
            将选材加入
            <select
              value={targetTopic}
              onChange={(event) => {
                const next = new URLSearchParams(params);
                next.set("addTo", event.target.value);
                add.reset();
                setParams(next);
              }}
            >
              <option value="">选择研究专题</option>
              {selectedTopic.data &&
                !topics.data?.items.some(
                  (value) => value.id === targetTopic,
                ) && (
                  <option value={targetTopic}>
                    {selectedTopic.data.title}
                  </option>
                )}
              {topics.data?.items.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title}
                </option>
              ))}
            </select>
          </label>
        </>
      )}
      {results.data?.hits.map((hit) => (
        <article key={hit.id} className="study-search-hit">
          <p className="study-eyebrow">
            {hit.book_title} · PDF {hit.pages.join("、")} 页
          </p>
          <h2>{hit.title}</h2>
          <blockquote>{hit.text}</blockquote>
          <div className="study-inline-actions">
            {hit.reading_target && (
              <Link
                to={readingHref(hit.book_id, hit.reading_target, {
                  returnTo: `${location.pathname}${location.search}`,
                })}
              >
                打开原文并定位
              </Link>
            )}
            {hit.research_cards?.map((card) => (
              <span key={card.id}>
                <Link
                  to={`/research?${new URLSearchParams({ card: card.id, returnTo: `${location.pathname}${location.search}` })}`}
                >
                  {card.title || "查看史料卡"}
                </Link>
                {targetTopic && (
                  <Button
                    variant="outline"
                    disabled={
                      add.isPending ||
                      !selectedTopic.data ||
                      selectedTopic.data.archived ||
                      selectedTopic.data.materials.some(
                        (material) => material.card_id === card.id,
                      )
                    }
                    onClick={() => add.mutate(card)}
                  >
                    {selectedTopic.data?.materials.some(
                      (material) => material.card_id === card.id,
                    )
                      ? "已加入专题"
                      : "加入专题"}
                  </Button>
                )}
              </span>
            ))}
          </div>
        </article>
      ))}
      {topics.data && topics.data.total > topics.data.items.length && (
        <p>
          当前选择器显示最近 {topics.data.items.length} 个专题。
          <Link to="/studies">管理全部专题</Link>
        </p>
      )}
      {results.data?.available && results.data.hits.length === 0 && (
        <p>
          {results.data.evidence_status === "no_published_sources"
            ? "当前范围暂无已发布资料。请先完成史料入库或调整专题选材。"
            : "当前范围未找到匹配原文。这不代表历史上不存在相关记载，可调整词语或资料范围。"}
        </p>
      )}
      {!q && (
        <p className="study-empty">
          输入词语开始查找；检索结果会保留原文出处和返回位置。
        </p>
      )}
    </section>
  );
}
