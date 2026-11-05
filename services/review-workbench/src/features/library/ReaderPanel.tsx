import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient, queryOptions } from "@tanstack/react-query";
import { Button } from "@/components/ui/button.tsx";
import { RenderedMarkdown } from "@/editor/RenderedMarkdown.tsx";
import { client } from "@/client/api.ts";
import StructurePanel from "@/features/library/StructurePanel.tsx";

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
  const requestedPage = Number(params.get("physicalPage"));
  const id =
    chapters.data?.find((item) => item.id === requested)?.id ||
    chapters.data?.find(
      (item) => requestedPage > 0 && item.pages.includes(requestedPage),
    )?.id ||
    chapters.data?.[0]?.id ||
    "";
  const chapter = useQuery({ ...chapterOptions(id), enabled: Boolean(id) });
  useEffect(() => {
    const anchor = params.get("anchor");
    if (!chapter.data || selected) return;
    const literal = (anchor ?? "")
      .replace(/^\s*#{1,6}\s+/, "")
      .replace(/^\*\*|\*\*$/g, "")
      .trim();
    const heading = [
      ...document.querySelectorAll(
        ".platform-reading article h1,.platform-reading article h2,.platform-reading article h3,.platform-reading article h4,.platform-reading article h5,.platform-reading article h6",
      ),
    ].find((e) => e.textContent?.trim() === literal);
    heading?.scrollIntoView({ block: "center" });
    if (requestedPage > 0) {
      setPage(requestedPage);
      setOriginal(true);
    }
  }, [chapter.data, params, selected]);
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
          重新读取章节
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
            href={`/api/v2/books/${bookId}/export?download=latest`}
          >
            导出全书 Markdown
          </a>
        </div>
        {chapter.data && (
          <StructurePanel
            key={id}
            runId={chapter.data.run_id}
            items={chapter.data.structure ?? []}
            onLocate={(value) => {
              setPage(value);
              setOriginal(true);
            }}
          />
        )}
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
                <RenderedMarkdown
                  markdown={chapter.data.reading_text ?? chapter.data.text}
                  footnotes={
                    chapter.data.reading_text != null
                      ? chapter.data.reading_footnotes
                      : chapter.data.footnotes
                  }
                  assetBaseUrl={`/api/v2/runs/${chapter.data.run_id}/artifacts`}
                />
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
                  {Array.from(
                    new Set([
                      ...chapter.data.pages,
                      ...(page == null ? [] : [page]),
                    ]),
                  )
                    .sort((a, b) => a - b)
                    .map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                </select>
              </label>
              <OriginalPage
                runId={chapter.data.run_id}
                page={page ?? chapter.data.pages[0]!}
              />
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}

function OriginalPage({ runId, page }: { runId: string; page: number }) {
  const original = useQuery({
    queryKey: ["platform", "original-page", runId, page],
    staleTime: Infinity,
    queryFn: async ({ signal }) => {
      const { data } = await client.GET(
        "/api/v2/runs/{run_id}/review-pages/{page}",
        { params: { path: { run_id: runId, page } }, signal },
      );
      if (!data) throw new Error("本页原图暂时无法读取，可重试或打开原 PDF。");
      return data.image;
    },
  });
  return (
    <figure>
      <a
        href={`/api/v2/runs/${runId}/artifacts/original.pdf#page=${page}`}
        target="_blank"
        rel="noreferrer"
      >
        打开原 PDF
      </a>
      {original.isPending ? (
        <p role="status">正在读取原书第 {page} 页…</p>
      ) : original.isError ? (
        <p role="alert">
          {original.error.message}
          <Button onClick={() => void original.refetch()}>重新读取原图</Button>
        </p>
      ) : (
        <a href={original.data} target="_blank" rel="noreferrer">
          <img src={original.data} alt={`原书第 ${page} 页`} />
        </a>
      )}
    </figure>
  );
}
