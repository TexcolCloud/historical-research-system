import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Link,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import { useQuery, useQueryClient, queryOptions } from "@tanstack/react-query";
import { Button } from "@/components/ui/button.tsx";
import { PanelLeftClose, PanelLeftOpen, BookOpen, Search } from "lucide-react";
import { RenderedMarkdown } from "@/editor/RenderedMarkdown.tsx";
import { client } from "@/client/api.ts";
import StructurePanel from "@/features/library/StructurePanel.tsx";
import SearchPanel from "@/features/search/SearchPanel.tsx";
import { readingNavigationOptions } from "./navigation-query";
import { navigationStatusLabel } from "./navigation-status";
import {
  readingHref,
  readingRange,
  requestedReadingTarget,
  safeReaderReturn,
  type ReadingTarget,
} from "./reader-location";
import type { components } from "@/client/schema";

function sourcePage(chapter: components["schemas"]["Chapter"], at: number) {
  let offset = 0;
  for (const part of chapter.parts) {
    const stop =
      offset +
      (typeof part.text === "string" ? Array.from(part.text).length : 0);
    const source = part.source;
    if (offset <= at && at < stop && source && typeof source === "object") {
      if (
        "page" in source &&
        typeof source.page === "number" &&
        Number.isSafeInteger(source.page) &&
        source.page > 0
      )
        return source.page;
      if ("pages" in source && Array.isArray(source.pages)) {
        const page = source.pages.find(
          (value: unknown) =>
            typeof value === "number" &&
            Number.isSafeInteger(value) &&
            value > 0,
        );
        if (typeof page === "number") return page;
      }
    }
    offset = stop;
  }
  return chapter.pages[0] ?? 1;
}

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
  const [params, setParams] = useSearchParams();
  const returnTo = safeReaderReturn(params.get("returnTo"), bookId);
  const returnParams = new URLSearchParams(
    returnTo.slice(returnTo.indexOf("?") + 1),
  );
  const returnLabel = returnTo.startsWith("/research")
    ? "返回研究检索"
    : returnParams.get("tab") === "research"
      ? "返回史料卡"
      : returnParams.get("section") === "search" ||
          returnParams.get("tab") === "search"
        ? "返回检索结果"
        : "返回目录";
  const location = useLocation(),
    navigate = useNavigate();
  // Drawer and query changes preserve the current reading position. Following a
  // source link still gets a fresh navigation key, including repeated hits.
  const focusNavigation = useRef(location.key);
  if (!location.state?.preserveReadingPosition)
    focusNavigation.current = location.key;
  const focusKey = focusNavigation.current;
  const [original, setOriginal] = useState(false),
    [page, setPage] = useState<number | null>(null);
  const [mappingFailures, setMappingFailures] = useState<string[]>([]);
  const [navigationOpen, setNavigationOpen] = useState(true);
  const searchOpen =
    params.get("section") === "search" || params.get("tab") === "search";
  const [navigationView, setNavigationView] = useState<
    "original" | "chapters" | null
  >(null);
  const navigationToggle = useRef<HTMLButtonElement>(null);
  const structureControl = useRef<HTMLDivElement>(null);
  const article = useRef<HTMLElement>(null);
  const [bodyHasTitle, setBodyHasTitle] = useState(false);
  const navigation = useQuery(readingNavigationOptions(bookId));
  const visibleNavigation =
    navigationView ??
    (navigation.isPending ||
    navigation.isError ||
    navigation.data?.entries.length
      ? "original"
      : "chapters");
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
  const requested = params.get("chapter");
  const requestedPage = Number(params.get("physicalPage"));
  const id =
    chapters.data?.find((item) => item.id === requested)?.id ||
    chapters.data?.find(
      (item) => requestedPage > 0 && item.pages.includes(requestedPage),
    )?.id ||
    chapters.data?.[0]?.id ||
    "";
  const chapter = useQuery({ ...chapterOptions(id), enabled: Boolean(id) });
  const sourcePages = useMemo(
    () => [...new Set(chapter.data?.pages ?? [])].sort((a, b) => a - b),
    [chapter.data?.pages],
  );
  const continuousPages = sourcePages.every(
    (value, at) => at === 0 || value === sourcePages[at - 1] + 1,
  );
  const pageSummary =
    sourcePages.length > 1 && continuousPages
      ? `PDF 第 ${sourcePages[0]}–${sourcePages.at(-1)} 页`
      : sourcePages.length > 3
        ? `PDF 第 ${sourcePages.slice(0, 3).join("、")} 等 ${sourcePages.length} 页`
        : `PDF 第 ${sourcePages.join("、")} 页`;
  useLayoutEffect(() => {
    // Compare the rendered heading, including inline Markdown and escaped entities.
    // Only suppress our metadata heading; the source DOM and offsets stay untouched.
    const heading = article.current
      ?.querySelector(".prose h1")
      ?.cloneNode(true);
    if (heading instanceof Element)
      for (const reference of heading.querySelectorAll(
        "a.footnote-reference[data-note-jump]",
      ))
        reference.remove();
    const plain = (value: string) => value.replace(/\s+/gu, " ").trim();
    setBodyHasTitle(
      Boolean(
        heading &&
        chapter.data &&
        plain(heading.textContent ?? "") === plain(chapter.data.title),
      ),
    );
  }, [chapter.data]);
  const target = useMemo(() => requestedReadingTarget(params), [params]);
  const invalidTarget =
    !target &&
    ["start", "end", "textSha", "run"].some((key) => params.has(key));
  const range = useMemo(
    () => (target && chapter.data ? readingRange(target, chapter.data) : null),
    [target, chapter.data],
  );
  const readingFocus = useMemo(
    () =>
      range
        ? {
            ...range,
            id: `${focusKey}:${target?.run_id}:${target?.chapter_id}:${target?.text_sha256}:${range.start}:${range.end}`,
          }
        : undefined,
    [range, focusKey, target],
  );
  const failedTarget = Boolean(
    chapter.data &&
    (invalidTarget ||
      (target &&
        (!range ||
          (readingFocus && mappingFailures.includes(readingFocus.id))))),
  );
  const inlineLinks = useMemo(() => {
    const current = chapter.data;
    if (!current) return [];
    return (current.toc_links ?? []).flatMap((link) => {
      const sourceTarget: ReadingTarget = {
        chapter_id: current.id,
        run_id: current.run_id,
        physical_page: sourcePage(current, link.source_start),
        start: link.source_start,
        end: link.source_end,
        text_sha256: current.text_sha256,
        anchor: "",
        image_sha256: "",
      };
      const sourceRange = readingRange(sourceTarget, current);
      if (!sourceRange) return [];
      return [
        {
          ...sourceRange,
          id: link.id,
          label: `阅读${link.title}`,
          href: `#${readingHref(bookId, link.target, { returnTo: readingHref(bookId, sourceTarget) })}`,
        },
      ];
    });
  }, [chapter.data, bookId]);
  useEffect(() => {
    if (!chapter.data) return;
    const anchor = params.get("anchor");
    const literal = (anchor ?? "")
      .replace(/^\s*#{1,6}\s+/, "")
      .replace(/^\*\*|\*\*$/g, "")
      .trim();
    const headings = [
      ...(article.current?.querySelectorAll("h1,h2,h3,h4,h5,h6") ?? []),
    ].filter((e) => e.textContent?.trim() === literal);
    if (!target && literal && headings.length === 1)
      headings[0].scrollIntoView?.({ block: "center" });
    if (requestedPage > 0 && Number.isSafeInteger(requestedPage)) {
      setPage(requestedPage);
      if (!target || failedTarget) setOriginal(true);
    }
  }, [chapter.data, params, requestedPage, target, failedTarget]);
  const index = chapters.data?.findIndex((item) => item.id === id) ?? -1;
  const next = chapters.data?.[index + 1],
    previous = chapters.data?.[index - 1];
  useEffect(() => {
    for (const entry of [next, previous])
      if (entry) void query.prefetchQuery(chapterOptions(entry.id));
  }, [next, previous, query]);
  function go(id: string) {
    const next = new URLSearchParams(params);
    for (const key of [
      "physicalPage",
      "start",
      "end",
      "textSha",
      "run",
      "anchor",
    ])
      next.delete(key);
    next.set("tab", "reader");
    next.set("section", searchOpen ? "search" : "text");
    next.set("chapter", id);
    if (params.has("returnTo")) next.set("returnTo", returnTo);
    setParams(next);
    setPage(null);
  }
  function toggleSearch(open: boolean) {
    const next = new URLSearchParams(params);
    next.set("tab", "reader");
    next.set("section", open ? "search" : "text");
    if (id) next.set("chapter", id);
    setParams(next, { state: { preserveReadingPosition: true } });
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
    <div
      className="platform-reader-grid"
      data-navigation-open={navigationOpen}
      data-search-open={searchOpen}
    >
      <nav aria-label="章节目录" hidden={!navigationOpen}>
        <div className="reader-navigation-heading">
          <h2>本书导航</h2>
          <Button
            variant="ghost"
            size="icon"
            aria-label="关闭导航"
            onClick={() => {
              setNavigationOpen(false);
              navigationToggle.current?.focus();
            }}
          >
            <PanelLeftClose size={18} />
          </Button>
        </div>
        <div
          className="reader-navigation-tabs"
          role="group"
          aria-label="导航方式"
        >
          <Button
            variant={visibleNavigation === "original" ? "default" : "ghost"}
            aria-pressed={visibleNavigation === "original"}
            onClick={() => setNavigationView("original")}
          >
            原书目录
          </Button>
          <Button
            variant={visibleNavigation === "chapters" ? "default" : "ghost"}
            aria-pressed={visibleNavigation === "chapters"}
            onClick={() => setNavigationView("chapters")}
          >
            章节
          </Button>
        </div>
        <div hidden={visibleNavigation !== "original"}>
          {navigation.isPending && <p role="status">正在读取原书目录…</p>}
          {navigation.isError && (
            <p role="alert">
              {navigation.error.message}
              <Button
                variant="outline"
                onClick={() => void navigation.refetch()}
              >
                重新读取目录
              </Button>
            </p>
          )}
          <ol className="reader-source-toc">
            {(navigation.data?.entries ?? []).map((entry) => (
              <li
                key={entry.id}
                style={{
                  paddingInlineStart: `${Math.max(0, entry.level - 1) * 0.75}rem`,
                }}
              >
                {entry.reading_target && !entry.needs_confirmation ? (
                  <Link
                    to={readingHref(bookId, entry.reading_target, {
                      returnTo: `/books/${bookId}?tab=reader&section=directory&tocEntry=${encodeURIComponent(entry.id)}`,
                    })}
                    aria-current={
                      target?.chapter_id === entry.reading_target.chapter_id &&
                      target.start === entry.reading_target.start
                        ? "location"
                        : undefined
                    }
                  >
                    {entry.title}
                    <small>书内页 {entry.target_label}</small>
                  </Link>
                ) : (
                  <>
                    <span>
                      {entry.title}
                      <small>
                        {entry.target_label} · {navigationStatusLabel(entry)}
                      </small>
                    </span>
                    {entry.needs_confirmation && (
                      <Link
                        className="reader-toc-management"
                        to={`/books/${bookId}?tab=review&task=navigation&entry=${encodeURIComponent(entry.id)}`}
                      >
                        核对位置
                      </Link>
                    )}
                    {!entry.needs_confirmation &&
                      (entry.target_image || entry.target_url) && (
                        <a
                          className="reader-toc-management"
                          href={
                            entry.target_image ?? entry.target_url ?? undefined
                          }
                          target="_blank"
                          rel="noreferrer"
                        >
                          查看对应原图
                        </a>
                      )}
                  </>
                )}
              </li>
            ))}
          </ol>
          {!navigation.isPending &&
            !navigation.isError &&
            !navigation.data?.entries.length && (
              <p className="reader-navigation-empty">
                本书没有可用的原书目录。可切换到章节阅读。
              </p>
            )}
        </div>
        <div
          className="reader-chapter-list"
          hidden={visibleNavigation !== "chapters"}
        >
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
        </div>
        <Link
          className="reader-directory-link"
          to={`/books/${bookId}?tab=reader&section=directory${id ? `&chapter=${encodeURIComponent(id)}` : ""}`}
        >
          查看完整目录与核对状态
        </Link>
      </nav>
      <div className="platform-reading">
        <div className="reader-toolbar">
          <Button
            ref={navigationToggle}
            variant="ghost"
            onClick={() => setNavigationOpen(!navigationOpen)}
            aria-expanded={navigationOpen}
          >
            {navigationOpen ? (
              <PanelLeftClose size={18} />
            ) : (
              <PanelLeftOpen size={18} />
            )}
            目录
          </Button>
          {params.has("returnTo") && (
            <Link to={returnTo} className="reading-return-link">
              {returnLabel}
            </Link>
          )}
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
          <Button
            variant={searchOpen ? "default" : "outline"}
            aria-expanded={searchOpen}
            aria-controls="reader-search-sidebar"
            onClick={() => toggleSearch(!searchOpen)}
          >
            <Search size={17} aria-hidden="true" />
            检索本书
          </Button>
          {chapter.data && (
            <div className="reader-structure-control" ref={structureControl}>
              <StructurePanel
                key={id}
                runId={chapter.data.run_id}
                items={chapter.data.structure ?? []}
                onLocate={(value) => {
                  setPage(value);
                  setOriginal(true);
                  const details =
                    structureControl.current?.querySelector("details");
                  if (details) details.open = false;
                }}
              />
            </div>
          )}
          <a
            className="platform-export"
            href={`/api/v2/books/${bookId}/export?download=latest`}
          >
            导出全书 Markdown
          </a>
        </div>
        {failedTarget && (
          <p role="status" className="workspace-status-note">
            定位依据已更新，或此位置无法精确对应阅读版。请结合原图核对，未标记其他文字。
          </p>
        )}
        {mappingFailures.some((value) => value !== readingFocus?.id) && (
          <p className="workspace-status-note">
            部分目录文字无法安全生成链接，可从左侧原书目录跳转。
          </p>
        )}
        <div className="reader-body" data-search-open={searchOpen}>
          <div
            className={
              original
                ? "platform-reading-columns"
                : "platform-reading-columns single"
            }
          >
            <article ref={article}>
              {chapter.data ? (
                <>
                  <div className="reading-provenance">
                    <BookOpen size={16} aria-hidden="true" />
                    <details className="reading-page-details">
                      <summary>{pageSummary}</summary>
                      <p>完整 PDF 页码：{sourcePages.join("、")}</p>
                    </details>
                  </div>
                  {!bodyHasTitle && <h1>{chapter.data.title}</h1>}
                  <RenderedMarkdown
                    markdown={chapter.data.reading_text ?? chapter.data.text}
                    footnotes={
                      chapter.data.reading_text != null
                        ? chapter.data.reading_footnotes
                        : chapter.data.footnotes
                    }
                    assetBaseUrl={`/api/v2/runs/${chapter.data.run_id}/artifacts`}
                    readingLinks={inlineLinks}
                    readingFocus={readingFocus}
                    onReadingFailure={setMappingFailures}
                    onReadingNavigate={(href) =>
                      navigate(href.startsWith("#") ? href.slice(1) : href)
                    }
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
                  runId={
                    failedTarget && target ? target.run_id : chapter.data.run_id
                  }
                  page={page ?? chapter.data.pages[0]!}
                />
              </aside>
            )}
          </div>
          {searchOpen && (
            <aside
              id="reader-search-sidebar"
              className="reader-search-sidebar"
              aria-label="本书检索侧栏"
            >
              <SearchPanel
                bookId={bookId}
                onClose={() => toggleSearch(false)}
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
