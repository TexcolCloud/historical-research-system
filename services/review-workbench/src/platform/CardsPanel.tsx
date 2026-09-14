import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Button } from "../components/ui/button";
import CardReading from "./CardReading";
import { client, readCards } from "./client";
import StartCardsButton from "./StartCardsButton";

export default function CardsPanel({
  bookId,
  published = false,
}: {
  bookId: string;
  published?: boolean;
}) {
  const [params, setParams] = useSearchParams();
  const cards = useQuery({
    queryKey: ["platform", "cards", bookId],
    queryFn: ({ signal }) => readCards(bookId, signal),
  });
  const requested = params.get("card");
  const id = requested
    ? cards.data?.find((row) => row.id === requested)?.id
    : cards.data?.[0]?.id;
  const card = useQuery({
    queryKey: ["platform", "card", id],
    enabled: Boolean(id),
    staleTime: 30_000,
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/cards/{card_id}", {
        params: { path: { card_id: id! } },
        signal,
      });
      if (!data) throw new Error("无法读取卡片详情。");
      return data;
    },
  });
  if (cards.isError)
    return (
      <div role="alert">
        <p>{cards.error.message}</p>
        <Button variant="outline" onClick={() => void cards.refetch()}>
          重新读取列表
        </Button>
      </div>
    );
  if (!cards.data?.length)
    return (
      <section className="platform-empty">
        <h2>{cards.isPending ? "正在读取卡片…" : "还没有生成的卡片"}</h2>
        <p>
          制卡任务完成后，卡片会显示在这里；已有任务可在“任务运行”中查看进展。
        </p>
        {published && !cards.isPending && (
          <StartCardsButton key={bookId} bookId={bookId} />
        )}
      </section>
    );
  return (
    <>
      <StartCardsButton key={bookId} bookId={bookId} />
      <div className="platform-reader-grid">
        <nav aria-label="本书史料卡">
          {cards.data.map((row) => (
            <button
              key={row.id}
              aria-current={row.id === id ? "true" : undefined}
              onClick={() => {
                const next = new URLSearchParams(params);
                next.set("card", row.id);
                setParams(next);
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
        {!id ? (
          <p role="alert">本书没有这张史料卡，请从左侧选择。</p>
        ) : card.isError ? (
          <div role="alert">
            <p>{card.error.message}</p>
            <Button variant="outline" onClick={() => void card.refetch()}>
              重新读取卡片
            </Button>
          </div>
        ) : card.isPending ? (
          <div className="platform-card-loading" role="status">
            正在读取所选卡片…
          </div>
        ) : (
          card.data && <CardReading key={card.data.id} card={card.data} />
        )}
      </div>
    </>
  );
}
