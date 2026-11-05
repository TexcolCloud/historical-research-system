import { useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { client } from "@/client/api.ts";
export default function SearchPanel({ bookId }: { bookId: string }) {
  const [text, setText] = useState(""),
    [request, setRequest] = useState({
      q: "",
      semantic: true,
      diverse: false,
      include_navigation: false,
    }),
    [includeNavigation, setIncludeNavigation] = useState(false),
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
          if (text.trim())
            setRequest({
              q: text.trim(),
              semantic,
              diverse,
              include_navigation: includeNavigation,
            });
        }}
      >
        <Input
          aria-label="检索本书正文"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="查找人物、事件或关键词"
        />
        <Button type="submit">查找</Button>
        <details className="workspace-search-options">
          <summary>检索选项</summary>
          <label>
            <input
              type="checkbox"
              checked={includeNavigation}
              onChange={(e) => setIncludeNavigation(e.target.checked)}
            />
            包含原书目录
          </label>
          <label>
            检索方式
            <select
              aria-label="检索方式"
              value={semantic ? "meaning" : "literal"}
              onChange={(e) => setSemantic(e.target.value === "meaning")}
            >
              <option value="meaning">按意思查找</option>
              <option value="literal">按原文查词</option>
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              checked={diverse}
              onChange={(e) => setDiverse(e.target.checked)}
            />
            跨章节综合
          </label>
          <p>调整选项后点击“查找”应用。</p>
        </details>
      </form>
      {results.isFetching && (
        <p role="status">
          {request.semantic ? "正在按意思查找相关内容…" : "正在按原文查找…"}
        </p>
      )}
      {results.isError && (
        <p role="alert">
          {results.error.message}
          <Button onClick={() => void results.refetch()}>重新查找</Button>
        </p>
      )}
      {results.data?.length === 0 && (
        <p>
          未找到相关内容。可缩短关键词，或在“检索方式”中选择“按原文查词”后重新查找。
        </p>
      )}
      {results.data?.map((hit) => (
        <article key={hit.id}>
          <h2>{hit.title}</h2>
          {hit.doc_kind === "navigation" && <p>原书目录条目</p>}
          {!!hit.toc_path?.length && (
            <p>原书目录：{hit.toc_path.join(" / ")}</p>
          )}
          {!!hit.printed_pages?.length && (
            <p>
              印刷页：
              {hit.printed_pages
                .map((p) => String(p.label ?? "未知"))
                .join("、")}
            </p>
          )}
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
                  <Link
                    to={`/books/${hit.book_id}?tab=reader&chapter=${hit.chapter_id}&physicalPage=${part.pages[0]}`}
                  >
                    查看原件第 {part.pages.join("、")} 页
                  </Link>
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
          <Link
            to={`/books/${hit.book_id}?tab=reader&chapter=${hit.chapter_id}&physicalPage=${hit.pages[0]}`}
          >
            查看原件第 {hit.pages.join("、")} 页
          </Link>
        </article>
      ))}
    </section>
  );
}
