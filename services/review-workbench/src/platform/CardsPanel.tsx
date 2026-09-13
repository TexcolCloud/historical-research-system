import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "../components/ui/button";
import { RenderedMarkdown } from "../editor/RenderedMarkdown";
import { client, readCards } from "./client";
import StartCardsButton from "./StartCardsButton";
import { useSearchParams } from "react-router-dom";
export default function CardsPanel({
  bookId,
  published = false,
}: {
  bookId: string;
  published?: boolean;
}) {
  const [params] = useSearchParams();
  const [selected, setSelected] = useState(""),
    [sourceId, setSourceId] = useState("");
  const cards = useQuery({
    queryKey: ["platform", "cards", bookId],
    queryFn: ({ signal }) => readCards(bookId, signal),
  });
  const id = selected || params.get("card") || cards.data?.[0]?.id || "";
  const card = useQuery({
    queryKey: ["platform", "card", id],
    enabled: Boolean(id),
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/cards/{card_id}", {
        params: { path: { card_id: id } },
        signal,
      });
      if (!data) throw new Error("无法读取卡片详情。");
      return data;
    },
  });
  const source = card.data?.units.find((unit) => unit.unit_id === sourceId);
  if (cards.isError || card.isError)
    return <p role="alert">{cards.error?.message || card.error?.message}</p>;
  if (!cards.data?.length)
    return (
      <section className="platform-empty">
        <h2>{cards.isPending ? "正在读取卡片…" : "还没有生成的卡片"}</h2>
        <p>本书入库后自动开始制卡，可在任务运行中查看实际分工和进展。</p>
        {published && !cards.isPending && <StartCardsButton bookId={bookId} />}
      </section>
    );
  return (
    <>
      <StartCardsButton bookId={bookId} />
      <div className="platform-reader-grid">
        <nav aria-label="本书史料卡">
          {cards.data.map((row) => (
            <button
              key={row.id}
              aria-current={row.id === id ? "true" : undefined}
              onClick={() => {
                setSelected(row.id);
                setSourceId("");
              }}
            >
              {row.title}
              <small>
                {row.state === "adopted"
                  ? "机器核验通过 · 可用"
                  : "机器核验未通过 · 待修复"}
              </small>
            </button>
          ))}
        </nav>
        <div className="platform-card-content">
          {card.data && (
            <article>
              <span className="platform-status">
                {card.data.state === "adopted"
                  ? "已通过机器核验"
                  : "此卡尚不可作为已核验成果使用"}
              </span>
              <h1>{card.data.title}</h1>
              <a
                className="platform-export"
                href={`/api/v2/cards/${card.data.id}/export`}
              >
                导出卡片 Markdown
              </a>
              <p>
                {card.data.candidate.document_type} ·{" "}
                {card.data.candidate.source_layer}
              </p>
              {card.data.candidate.items.map((item) => (
                <section key={item.item_id} className="platform-card-item">
                  <h2>{item.title}</h2>
                  <RenderedMarkdown markdown={item.text} />
                  {item.interpretation && <p>{item.interpretation}</p>}
                  {item.limitations?.map((limit) => (
                    <p className="platform-inline-status" key={limit}>
                      {limit}
                    </p>
                  ))}
                  {item.selections?.map((quote, index) => (
                    <blockquote key={index}>
                      <RenderedMarkdown markdown={quote.quote} />
                      <Button
                        variant="link"
                        onClick={() => setSourceId(quote.unit_id)}
                      >
                        查看引文出处
                      </Button>
                    </blockquote>
                  ))}
                </section>
              ))}
              <details>
                <summary>机器核验记录</summary>
                <pre>{JSON.stringify(card.data.verdict, null, 2)}</pre>
              </details>
            </article>
          )}
          {source && (
            <aside className="platform-card-source">
              <div className="platform-review-actions">
                <strong>
                  {source.title} · 原件 {source.pages.join("、")} 页
                </strong>
                <Button variant="ghost" onClick={() => setSourceId("")}>
                  收起出处
                </Button>
              </div>
              <RenderedMarkdown markdown={source.text} />
              <a
                target="_blank"
                rel="noreferrer"
                href={`/api/v2/runs/${source.run_id}/artifacts/original.pdf#page=${source.pages[0]}`}
              >
                对照原书
              </a>
            </aside>
          )}
        </div>
      </div>
    </>
  );
}
