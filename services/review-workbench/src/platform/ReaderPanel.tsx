import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient, queryOptions } from "@tanstack/react-query";
import { Button } from "../components/ui/button";
import { RenderedMarkdown } from "../editor/RenderedMarkdown";
import { client } from "./client";

const chapterOptions = (id: string) =>
  queryOptions({
    queryKey: ["platform", "chapter", id],
    staleTime: Infinity,
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/chapters/{chapter_id}", {
        params: { path: { chapter_id: id } },
        signal,
      });
      if (!data) throw new Error("章节暂时无法读取，请重试。");
      return data;
    },
  });
export default function ReaderPanel({ bookId }: { bookId: string }) {
  const query = useQueryClient();
  const [params] = useSearchParams();
  const [selected, setSelected] = useState(""),
    [original, setOriginal] = useState(false),
    [page, setPage] = useState<number | null>(null);
  const chapters = useQuery({
    queryKey: ["platform", "chapters", bookId],
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/books/{book_id}/chapters", {
        params: { path: { book_id: bookId } },
        signal,
      });
      if (!data) throw new Error("目录暂时无法读取，请重试。");
      return data;
    },
  });
  const requested = selected || params.get("chapter");
  const id =
    chapters.data?.find((item) => item.id === requested)?.id ||
    chapters.data?.[0]?.id ||
    "";
  const chapter = useQuery({ ...chapterOptions(id), enabled: Boolean(id) });
  const index = chapters.data?.findIndex((item) => item.id === id) ?? -1;
  const next = chapters.data?.[index + 1],
    previous = chapters.data?.[index - 1];
  useEffect(() => {
    for (const entry of [next, previous])
      if (entry) void query.prefetchQuery(chapterOptions(entry.id));
  }, [next, previous, query]);
  function go(id: string) {
    setSelected(id);
    setPage(null);
  }
  if (chapters.isError || chapter.isError)
    return (
      <p role="alert">
        {chapters.error?.message || chapter.error?.message}
        <Button
          onClick={() => {
            void chapters.refetch();
            if (id) void chapter.refetch();
          }}
        >
          重试
        </Button>
      </p>
    );
  if (chapters.data?.length === 0)
    return (
      <section className="platform-empty">
        <h2>本书尚未入库</h2>
        <p>内容核对完成后，系统会自动组织整书章节并发布阅读版。</p>
      </section>
    );
  return (
    <div className="platform-reader-grid">
      <nav aria-label="章节目录">
        <h2>目录</h2>
        {chapters.data?.map((item) => (
          <button
            key={item.id}
            aria-current={item.id === id ? "true" : undefined}
            onPointerEnter={() =>
              void query.prefetchQuery(chapterOptions(item.id))
            }
            onClick={() => go(item.id)}
          >
            {item.title}
            <small>
              原件 {item.pages[0]}–{item.pages.at(-1)} 页
            </small>
          </button>
        ))}
      </nav>
      <div className="platform-reading">
        <div className="platform-review-actions">
          <Button
            variant="outline"
            disabled={!previous}
            onClick={() => previous && go(previous.id)}
          >
            上一章
          </Button>
          <Button
            variant="outline"
            disabled={!next}
            onClick={() => next && go(next.id)}
          >
            下一章
          </Button>
          <Button variant="outline" onClick={() => setOriginal(!original)}>
            {original ? "收起原书" : "对照原书"}
          </Button>
          <a
            className="platform-export"
            href={`/api/v2/books/${bookId}/export`}
          >
            导出全书 Markdown
          </a>
        </div>
        <div
          className={
            original
              ? "platform-reading-columns"
              : "platform-reading-columns single"
          }
        >
          <article>
            {chapter.data ? (
              <>
                <h1>{chapter.data.title}</h1>
                <RenderedMarkdown markdown={chapter.data.text} />
              </>
            ) : (
              <p role="status">正在读取章节…</p>
            )}
          </article>
          {original && chapter.data && (
            <aside>
              <label>
                关联原件页{" "}
                <select
                  aria-label="关联原件页"
                  value={page ?? chapter.data.pages[0]}
                  onChange={(e) => setPage(Number(e.target.value))}
                >
                  {chapter.data.pages.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <iframe
                title="原书 PDF"
                src={`/api/v2/runs/${chapter.data.run_id}/artifacts/original.pdf#page=${page ?? chapter.data.pages[0]}`}
              />
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}
