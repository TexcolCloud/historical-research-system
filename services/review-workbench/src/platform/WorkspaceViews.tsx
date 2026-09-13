import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowUpRight, ClipboardCheck, NotebookPen } from "lucide-react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import { readCards, readBooks } from "./client";
import { booksKey } from "./events";

export function ReviewInbox() {
  const books = useQuery({
    queryKey: booksKey,
    queryFn: ({ signal }) => readBooks(signal),
  });
  const pending =
    books.data?.filter((book) =>
      ["awaiting_review", "failed"].includes(book.state),
    ) ?? [];
  return (
    <>
      <div className="platform-heading">
        <div>
          <p className="platform-eyebrow">需要你关注的内容</p>
          <h1>待办</h1>
          <p>
            内容核对和处理异常按书籍集中显示，已通过的部分不会重复要求确认。
          </p>
        </div>
      </div>
      {books.isError ? (
        <div role="alert">
          <p>{books.error.message}</p>
          <Button onClick={() => void books.refetch()}>重试</Button>
        </div>
      ) : books.isPending ? (
        <p role="status">正在读取待办…</p>
      ) : !pending.length ? (
        <section className="platform-empty">
          <ClipboardCheck size={36} />
          <h2>暂时没有需要处理的内容</h2>
          <p>书籍核验发现问题后，会在这里列出。</p>
          <Link to="/">查看书籍进展</Link>
        </section>
      ) : (
        <section className="platform-collection" aria-label="按书籍整理的待办">
          {pending.map((book) => (
            <Link
              className="platform-collection-row"
              key={book.id}
              to={`/books/${book.id}?tab=${book.state === "failed" ? "progress" : "review"}`}
            >
              <ClipboardCheck size={24} />
              <div>
                <h2>{book.title}</h2>
                <p>
                  {book.state === "failed"
                    ? "处理未完成 · 查看原因并恢复"
                    : "需要人工核对 · 对照原件逐项处理"}
                </p>
              </div>
              <ArrowUpRight size={18} />
            </Link>
          ))}
        </section>
      )}
    </>
  );
}

export function CardCollection() {
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
