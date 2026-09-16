import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowUpRight, NotebookPen } from "lucide-react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs.tsx";
import { readBooks, readCards } from "@/client/api.ts";
import { booksKey } from "@/hooks/events.ts";

export default function CardsPage() {
  const [filter, setFilter] = useState("adopted"),
    [search, setSearch] = useState("");
  const books = useQuery({
    queryKey: booksKey,
    queryFn: ({ signal }) => readBooks(signal),
  });
  const cards = useQuery({
    queryKey: ["platform", "cards", "all"],
    queryFn: ({ signal }) => readCards(undefined, signal),
  });
  const visible = (cards.data ?? []).filter(
    (card) =>
      (filter === "adopted"
        ? card.state === "adopted"
        : card.state !== "adopted") &&
      card.title.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <>
      <div className="platform-heading">
        <div>
          <p className="platform-eyebrow">从材料到可追溯的认识</p>
          <h1>史料卡</h1>
          <p>从卡片返回书籍、引文和原件，随时核实研究依据。</p>
        </div>
      </div>
      <div className="platform-toolbar">
        <Tabs value={filter} onValueChange={setFilter}>
          <TabsList aria-label="卡片状态">
            <TabsTrigger value="adopted">可用卡片</TabsTrigger>
            <TabsTrigger value="pending">机器核验未通过</TabsTrigger>
          </TabsList>
        </Tabs>
        <Input
          className="platform-collection-search"
          aria-label="搜索史料卡"
          placeholder="搜索卡片标题…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      {cards.isError ? (
        <div role="alert">
          <p>{cards.error.message}</p>
          <Button onClick={() => void cards.refetch()}>重试</Button>
        </div>
      ) : cards.isPending ? (
        <p role="status">正在读取史料卡…</p>
      ) : !visible.length ? (
        <section className="platform-empty">
          <NotebookPen size={36} />
          <h2>
            {search
              ? "没有符合条件的卡片"
              : filter === "adopted"
                ? "暂无可用卡片"
                : "没有待修复的候选卡"}
          </h2>
          <p>本书入库后会自动开始制卡，处理进展保留在所属书籍中。</p>
          <Link to="/">查看书籍</Link>
        </section>
      ) : (
        <section className="platform-collection" aria-label="史料卡列表">
          {visible.map((card) => (
            <Link
              className="platform-collection-row"
              key={card.id}
              to={`/books/${card.book_id}?tab=cards&card=${card.id}`}
            >
              <NotebookPen size={24} />
              <div>
                <h2>{card.title}</h2>
                <p>
                  {books.data?.find((book) => book.id === card.book_id)
                    ?.title ?? "查看所属书籍"}
                </p>
                <span className="platform-status">
                  {card.state === "adopted"
                    ? "机器核验通过"
                    : "候选结果 · 尚未通过核验"}
                </span>
              </div>
              <ArrowUpRight size={18} />
            </Link>
          ))}
        </section>
      )}
    </>
  );
}
