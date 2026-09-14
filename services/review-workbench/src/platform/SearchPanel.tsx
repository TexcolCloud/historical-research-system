import { useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { client } from "./client";
export default function SearchPanel({ bookId }: { bookId: string }) {
  const [text, setText] = useState(""),
    [request, setRequest] = useState({ q: "", semantic: true, diverse: false }),
    [semantic, setSemantic] = useState(true),
    [diverse, setDiverse] = useState(false);
  const results = useQuery({
    queryKey: ["platform", "search", bookId, request],
    placeholderData: keepPreviousData,
    enabled: Boolean(request.q),
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/search", {
        params: { query: { ...request, book_id: bookId } },
        signal,
      });
      if (!data) throw new Error("检索暂时未完成，请重试。");
      return data;
    },
  });
  return (
    <section className="platform-search-panel">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (text.trim()) setRequest({ q: text.trim(), semantic, diverse });
        }}
      >
        <Input
          aria-label="检索本书正文"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="查找人物、事件或关键词"
        />
        <Button type="submit">查找</Button>
        <label>
          <input
            type="checkbox"
            checked={semantic}
            onChange={(e) => setSemantic(e.target.checked)}
          />
          研究检索（关闭后快速查词）
        </label>
        <label>
          <input
            type="checkbox"
            checked={diverse}
            onChange={(e) => setDiverse(e.target.checked)}
          />
          跨章节综合
        </label>
      </form>
      {results.isFetching && (
        <p role="status">
          {request.semantic ? "本地模型正在检索并重排相关内容…" : "正在查找…"}
        </p>
      )}
      {results.isError && (
        <p role="alert">
          {results.error.message}
          <Button onClick={() => void results.refetch()}>重试</Button>
        </p>
      )}
      {results.data?.length === 0 && (
        <p>没有找到相关内容。请调整关键词，或关闭研究检索后按原文查找。</p>
      )}
      {results.data?.map((hit) => (
        <article key={hit.id}>
          <h2>{hit.title}</h2>
          {(hit.section_path?.length ?? 0) > 1 && (
            <p>{hit.section_path?.join(" / ")}</p>
          )}
          <p style={{ whiteSpace: "pre-wrap" }}>{hit.text}</p>
          {(hit.context?.length ?? 0) > 0 && (
            <details>
              <summary>查看相关上下文、表头与注释</summary>
              {hit.context?.map((part) => (
                <div key={`${part.start}:${part.end}`}>
                  <p style={{ whiteSpace: "pre-wrap" }}>{part.text}</p>
                  <a
                    target="_blank"
                    rel="noreferrer"
                    href={`/api/v2/runs/${hit.run_id}/artifacts/original.pdf#page=${part.pages[0]}`}
                  >
                    查看原件第 {part.pages.join("、")} 页
                  </a>
                </div>
              ))}
            </details>
          )}
          {hit.context_truncated && <p>部分上下文较长，请打开章节继续阅读。</p>}
          <Link
            to={`/books/${hit.book_id}?tab=reader&chapter=${hit.chapter_id}`}
          >
            阅读所在章节
          </Link>
          <a
            target="_blank"
            rel="noreferrer"
            href={`/api/v2/runs/${hit.run_id}/artifacts/original.pdf#page=${hit.pages[0]}`}
          >
            查看原件第 {hit.pages.join("、")} 页
          </a>
        </article>
      ))}
    </section>
  );
}
