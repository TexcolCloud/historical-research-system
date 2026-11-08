import { useEffect, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { readingHref } from "@/features/library/reader-location";
import { research } from "./api";
import BookScope from "./BookScope";
import QuestionPicker from "./QuestionPicker";

export default function ResearchSearch() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const q = params.get("q") ?? "",
    book = params.get("book_id") ?? "";
  const books = [...new Set(params.getAll("book_ids"))];
  const scope = books.length ? books : book;
  const [text, setText] = useState(q);
  useEffect(() => setText(q), [q]);
  const results = useQuery({
    queryKey: ["research", "search", q, scope],
    enabled: Boolean(q),
    staleTime: 30000,
    refetchOnWindowFocus: false,
    queryFn: ({ signal }) => research.search(q, scope, signal),
  });
  const returnTo = `${location.pathname}${location.search}`;
  const noSources = results.data?.evidence_status === "no_published_sources";
  function select(key: "card" | "entity", value: string) {
    const next = new URLSearchParams(params);
    next.delete("card");
    next.delete("entity");
    next.set(key, value);
    setParams(next);
  }
  return (
    <section className="research-search">
      <div className="platform-heading">
        <div>
          <h1>研究检索</h1>
        </div>
        <Search size={22} aria-hidden="true" />
      </div>
      <form
        className="research-search-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!text.trim()) return;
          const next = new URLSearchParams(params);
          next.set("q", text.trim());
          next.delete("card");
          next.delete("entity");
          setParams(next);
          if (text.trim() === q) void results.refetch({ cancelRefetch: false });
        }}
      >
        <Input
          aria-label="检索全库原文与史料卡"
          maxLength={1000}
          placeholder="查找原文、史料卡、人物或事件"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <Button type="submit">查找</Button>
      </form>
      <div className="research-search-scope">
        <span>
          {books.length
            ? `当前范围：${books.length} 本书籍`
            : book
              ? "当前范围：所选书籍"
              : "当前范围：全部已入库书籍"}
        </span>
        <BookScope
          value={books.length ? books : book ? [book] : []}
          onApply={(selected) => {
            const next = new URLSearchParams(params);
            next.delete("book_id");
            next.delete("book_ids");
            next.delete("card");
            next.delete("entity");
            selected.forEach((value) => next.append("book_ids", value));
            setParams(next);
          }}
        />
        {(book || books.length > 0) && (
          <Button
            variant="ghost"
            onClick={() => {
              const next = new URLSearchParams(params);
              next.delete("book_id");
              next.delete("book_ids");
              next.delete("card");
              next.delete("entity");
              setParams(next);
            }}
          >
            改查全库
          </Button>
        )}
      </div>
      {!q && (
        <p className="research-empty">
          输入关键词查找已入库原文、史料卡及人物事件。
        </p>
      )}
      {results.isFetching && <p role="status">正在检索…</p>}
      {results.isError && (
        <p role="alert">
          {results.error.message}
          <Button variant="outline" onClick={() => void results.refetch()}>
            重新检索
          </Button>
        </p>
      )}
      {results.data && (
        <>
          {noSources ? (
            <div className="research-empty">
              <p>当前范围尚无已发布并完成索引的原文，请先完成入库。</p>
              <Link className="research-source-link" to="/">
                查看书籍处理进展
              </Link>
            </div>
          ) : (
            <p className="research-result-count">
              {results.data.backend === "hybrid"
                ? "原文：语义与关键词综合检索"
                : "原文：关键词检索"}{" "}
              {results.data.research_backend === "hybrid"
                ? "· 卡片与人物事件：语义与关键词综合检索"
                : results.data.research_backend === "lexical"
                  ? "· 卡片与人物事件：关键词检索"
                  : "· 卡片和人物事件按已保存文字匹配"}
            </p>
          )}
          {results.data.research_index &&
            results.data.research_index.state !== "ready" && (
              <p role="status" className="workspace-status-note">
                {results.data.research_index.state === "building"
                  ? "研究索引正在构建，原文仍可检索。"
                  : results.data.research_index.state === "partial"
                    ? "部分研究索引尚未就绪，当前结果可能不完整。"
                    : "研究索引暂不可用，保留已保存文字的明确匹配结果。"}
              </p>
            )}
          {!noSources &&
            !results.data.hits.length &&
            !results.data.cards.length &&
            !results.data.entities.length && (
              <p>没有找到相关内容。可缩短关键词后重试。</p>
            )}
          {[
            { kind: "person", label: "人物" },
            { kind: "event", label: "事件" },
            { kind: "other", label: "机构与地点" },
          ].map(({ kind, label }) => {
            const entities = results.data.entities.filter((item) =>
              kind === "other"
                ? !["person", "event"].includes(item.kind)
                : item.kind === kind,
            );
            return (
              entities.length > 0 && (
                <section aria-label={`${label}结果`} key={kind}>
                  <h2>{label}</h2>
                  <div className="research-entity-chips">
                    {entities.map((entity) => (
                      <Button
                        key={entity.id}
                        variant="outline"
                        onClick={() => select("entity", entity.id)}
                      >
                        {entity.name}
                        {entity.aliases?.length ? (
                          <small>
                            别名：
                            {entity.aliases
                              .map((alias) => alias.name)
                              .join("、")}
                          </small>
                        ) : null}
                      </Button>
                    ))}
                  </div>
                </section>
              )
            );
          })}
          {!!results.data.cards.length && (
            <section aria-label="史料卡结果">
              <h2>史料卡</h2>
              {results.data.cards.map((card) => (
                <article className="research-result" key={card.id}>
                  <button
                    className="research-result-title"
                    onClick={() => select("card", card.id)}
                  >
                    {card.title}
                  </button>
                  <p>{card.summary}</p>
                  <QuestionPicker
                    material={{
                      kind: "card",
                      target_id: card.id,
                      title: card.title,
                    }}
                  />
                  <Link
                    to={`/books/${encodeURIComponent(card.book_id)}?tab=research&card=${encodeURIComponent(card.id)}`}
                  >
                    查看所属书籍史料卡
                  </Link>
                </article>
              ))}
            </section>
          )}
          {!!results.data.hits.length && (
            <section aria-label="原文结果">
              <h2>原文</h2>
              {results.data.hits.map((hit) => (
                <article className="research-result" key={hit.id}>
                  <h3>{hit.title}</h3>
                  <p className="research-provenance">
                    PDF 第 {hit.pages.join("、")} 页
                  </p>
                  <p className="research-quote">{hit.text}</p>
                  {hit.reading_target && (
                    <QuestionPicker
                      material={{
                        kind: "source",
                        target_id: hit.reading_target.chapter_id,
                        title: hit.title,
                        start: hit.reading_target.start,
                        end: hit.reading_target.end,
                        text_sha256: hit.reading_target.text_sha256,
                      }}
                    />
                  )}
                  {hit.reading_target && (
                    <Link
                      className="research-source-link"
                      to={readingHref(hit.book_id, hit.reading_target, {
                        returnTo,
                      })}
                    >
                      阅读命中原文
                    </Link>
                  )}
                  <Link
                    to={`/research?${new URLSearchParams({ q, book_id: hit.book_id })}`}
                  >
                    只查此书
                  </Link>
                </article>
              ))}
            </section>
          )}
        </>
      )}
    </section>
  );
}
