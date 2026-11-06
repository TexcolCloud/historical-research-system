import { useEffect, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { client } from "@/client/api.ts";
import { readingHref } from "@/features/library/reader-location";
import { readingNavigationOptions } from "@/features/library/navigation-query";
import { ArrowUpRight, Search, X } from "lucide-react";
export default function SearchPanel({
  bookId,
  onClose,
}: {
  bookId: string;
  onClose?: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const request = {
    q: params.get("q") ?? "",
    semantic: params.get("semantic") !== "false",
    diverse: params.get("diverse") === "true",
    include_navigation: params.get("include_navigation") === "true",
    toc_entry_id: params.get("toc_entry_id") || undefined,
  };
  const [text, setText] = useState(request.q),
    [includeNavigation, setIncludeNavigation] = useState(
      request.include_navigation,
    ),
    [semantic, setSemantic] = useState(request.semantic),
    [diverse, setDiverse] = useState(request.diverse),
    [scope, setScope] = useState(request.toc_entry_id ?? "");
  useEffect(() => {
    setText(request.q);
    setIncludeNavigation(request.include_navigation);
    setSemantic(request.semantic);
    setDiverse(request.diverse);
    setScope(request.toc_entry_id ?? "");
  }, [
    request.q,
    request.semantic,
    request.diverse,
    request.include_navigation,
    request.toc_entry_id,
  ]);
  const navigation = useQuery(readingNavigationOptions(bookId));
  const entries = navigation.data?.entries ?? [];
  const scopes = entries.filter((entry) => entry.reading_target);
  function rangeAvailable(identity: string) {
    const index = entries.findIndex((entry) => entry.id === identity);
    const entry = entries[index];
    const following = entries
      .slice(index + 1)
      .find((next) => next.level <= entry.level);
    return !following || Boolean(following.reading_target);
  }
  const scopeName = request.toc_entry_id
    ? (scopes
        .find((entry) => entry.id === request.toc_entry_id)
        ?.path.join(" / ") ?? "所选目录（需重新确认）")
    : "全书";
  const returnParams = new URLSearchParams(params);
  returnParams.delete("returnTo");
  returnParams.set("tab", "reader");
  returnParams.set("section", "search");
  const returnTo = `${location.pathname}?${returnParams}`;
  function chapterHref(book: string, chapter: string, page?: number) {
    const next = new URLSearchParams({
      tab: "reader",
      section: "text",
      chapter,
      returnTo,
    });
    if (page != null) next.set("physicalPage", String(page));
    return `/books/${book}?${next}`;
  }
  const results = useQuery({
    queryKey: ["platform", "search", bookId, request],
    placeholderData: keepPreviousData,
    enabled: Boolean(request.q),
    queryFn: async ({ signal }) => {
      const { data, response } = await client.GET("/api/v2/search", {
        params: { query: { ...request, book_id: bookId } },
        signal,
      });
      if (!data)
        throw new Error(
          response.status === 409
            ? "所选目录范围已更新，请重新选择范围后查找。"
            : "检索暂时未完成，请重试。",
        );
      return data;
    },
  });
  return (
    <section className="platform-search-panel">
      <header className="search-workspace-heading">
        <div>
          <h2>检索本书</h2>
        </div>
        {onClose ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label="关闭检索"
            onClick={onClose}
          >
            <X size={18} aria-hidden="true" />
          </Button>
        ) : (
          <Search size={20} aria-hidden="true" />
        )}
      </header>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!text.trim()) return;
          const next = new URLSearchParams(params);
          next.set("tab", "reader");
          next.set("section", "search");
          next.set("q", text.trim());
          next.set("semantic", String(semantic));
          next.set("diverse", String(diverse));
          next.set("include_navigation", String(includeNavigation && !scope));
          if (scope) next.set("toc_entry_id", scope);
          else next.delete("toc_entry_id");
          setParams(next, { state: { preserveReadingPosition: true } });
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
            检索范围
            <select
              aria-label="检索范围"
              value={scope}
              onChange={(event) => setScope(event.target.value)}
            >
              <option value="">全书</option>
              {scopes.map((entry) => (
                <option
                  key={entry.id}
                  value={entry.id}
                  disabled={!rangeAvailable(entry.id)}
                >
                  {entry.path.join(" / ")}
                  {rangeAvailable(entry.id) ? "（含下级）" : "（范围待确认）"}
                </option>
              ))}
              {scope && !scopes.some((entry) => entry.id === scope) && (
                <option value={scope}>所选目录（需重新确认）</option>
              )}
            </select>
          </label>
          {navigation.isError && (
            <p>
              目录范围暂时无法读取，仍可检索全书。
              <Button type="button" onClick={() => void navigation.refetch()}>
                重新读取目录
              </Button>
            </p>
          )}
          <label>
            <input
              type="checkbox"
              checked={includeNavigation && !scope}
              disabled={Boolean(scope)}
              onChange={(e) => setIncludeNavigation(e.target.checked)}
            />
            包含原书目录
          </label>
          {scope && <p>限定目录后检索对应正文及关联注释。</p>}
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
      {request.q && (
        <p className="search-current-scope">
          当前检索范围：{scopeName}
          {request.toc_entry_id ? "（含下级目录）" : ""}
        </p>
      )}
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
      {!results.isPlaceholderData &&
        results.data?.map((hit) => (
          <article key={hit.id} className="search-result">
            <h2>{hit.title}</h2>
            <div className="search-result-provenance">
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
              <p>PDF 第 {hit.pages.join("、")} 页</p>
            </div>
            <p className="search-result-excerpt">{hit.text}</p>
            {hit.reading_target && (
              <Link
                className="search-primary-link"
                to={readingHref(hit.book_id, hit.reading_target, { returnTo })}
              >
                {hit.doc_kind === "navigation"
                  ? "阅读对应正文"
                  : "阅读命中段落"}
                <ArrowUpRight size={17} aria-hidden="true" />
              </Link>
            )}
            {(hit.context?.length ?? 0) > 0 && (
              <details>
                <summary>查看相关上下文、表头与注释</summary>
                {hit.context?.map((part) => (
                  <div key={`${part.start}:${part.end}`}>
                    <p style={{ whiteSpace: "pre-wrap" }}>{part.text}</p>
                    {part.outside_scope && (
                      <p>关联上下文 · 位于所选目录范围外</p>
                    )}
                    {part.reading_target && (
                      <Link
                        to={readingHref(hit.book_id, part.reading_target, {
                          returnTo,
                        })}
                      >
                        阅读这段上下文
                      </Link>
                    )}
                    <Link
                      to={chapterHref(
                        hit.book_id,
                        hit.chapter_id,
                        part.pages[0],
                      )}
                    >
                      查看原件第 {part.pages.join("、")} 页
                    </Link>
                  </div>
                ))}
              </details>
            )}
            {hit.context_truncated && (
              <p>部分上下文较长，请打开章节继续阅读。</p>
            )}
            {hit.doc_kind === "navigation" &&
              !hit.reading_target &&
              !!hit.directory_links?.length && (
                <ul aria-label="目录对应正文">
                  {hit.directory_links.map((entry) => (
                    <li key={entry.id}>
                      <Link
                        to={readingHref(hit.book_id, entry.target, {
                          returnTo,
                        })}
                      >
                        阅读正文：{entry.title}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            {hit.doc_kind === "navigation" &&
              !hit.reading_target &&
              !hit.directory_links?.length && (
                <p>这段目录尚未可靠关联正文，可先查看目录原件。</p>
              )}
            <footer className="search-result-links">
              <Link to={chapterHref(hit.book_id, hit.chapter_id)}>
                阅读所在章节
              </Link>
              <Link to={chapterHref(hit.book_id, hit.chapter_id, hit.pages[0])}>
                {hit.doc_kind === "navigation"
                  ? "查看目录原件第 "
                  : "查看原件第 "}
                {hit.pages.join("、")} 页
              </Link>
            </footer>
          </article>
        ))}
    </section>
  );
}
