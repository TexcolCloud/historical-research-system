import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Link,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import { Button } from "@/components/ui/button.tsx";
import { PanelLeftClose, PanelLeftOpen, BookOpen, Search } from "lucide-react";
import { RenderedMarkdown } from "@/editor/RenderedMarkdown.tsx";
import StructurePanel from "@/features/library/StructurePanel.tsx";
import OriginalPage from "./OriginalPage";
import { researchReadingLinks } from "@/features/research/reading";
import ReadingResearchSidebar from "@/features/research/ReadingResearchSidebar";
import SearchPanel from "@/features/search/SearchPanel.tsx";
import {
  readingHref,
  readingRange,
  safeReaderReturn,
  sourcePage,
  type ReadingTarget,
} from "./reader-location";
import ReaderNavigation from "./ReaderNavigation";
import { useReaderData } from "./useReaderData";
import { useReaderTarget } from "./useReaderTarget";

export default function ReaderPanel({ bookId }: { bookId: string }) {
  const [params, setParams] = useSearchParams();
  const requestedPage = Number(params.get("physicalPage"));
  const returnTo = safeReaderReturn(params.get("returnTo"), bookId);
  const returnParams = new URLSearchParams(
    returnTo.slice(returnTo.indexOf("?") + 1),
  );
  const returnLabel =
    /^\/(studies|arguments|timeline|analysis|search)(\/|\?|$)/.test(returnTo)
      ? returnTo.includes("/canvas")
        ? "返回专题画布"
        : returnTo.startsWith("/search")
          ? "返回全文检索"
          : "返回专题研究"
      : returnTo.startsWith("/library?")
        ? "返回史料卡"
        : returnTo.startsWith("/research")
          ? returnParams.get("view") === "questions"
            ? "返回历史记录"
            : returnParams.get("view") === "network"
              ? "返回关系与时间线"
              : "返回卡片与实体"
          : returnParams.get("tab") === "research"
            ? "返回史料卡"
            : returnParams.get("section") === "search" ||
                returnParams.get("tab") === "search"
              ? "返回检索结果"
              : "返回目录";
  const location = useLocation(),
    navigate = useNavigate();
  const [original, setOriginal] = useState(
      params.get("readerMode") === "compare",
    ),
    [page, setPage] = useState<number | null>(null);
  const [navigationOpen, setNavigationOpen] = useState(true);
  const searchOpen =
    params.get("section") === "search" || params.get("tab") === "search";
  const researchOpen =
    !searchOpen && params.get("readerInspector") === "related";
  const [navigationView, setNavigationView] = useState<
    "original" | "chapters" | null
  >(null);
  const navigationToggle = useRef<HTMLButtonElement>(null);
  const structureControl = useRef<HTMLDivElement>(null);
  const article = useRef<HTMLElement>(null);
  const [bodyHasTitle, setBodyHasTitle] = useState(false);
  const data = useReaderData(bookId, params);
  const { navigation, chapters, chapter, researchReading, id, next, previous } =
    data;
  const visibleNavigation =
    navigationView ??
    (navigation.isPending ||
    navigation.isError ||
    navigation.data?.entries.length
      ? "original"
      : "chapters");
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
  const {
    target,
    readingFocus,
    invalidTarget,
    failedTarget,
    targetSourceChanged,
    mappingFailures,
    setMappingFailures,
  } = useReaderTarget(chapter.data, params);
  const inlineLinks = useMemo(() => {
    const current = chapter.data;
    if (!current) return [];
    const directory = (current.toc_links ?? []).flatMap((link) => {
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
    return [
      ...directory,
      ...researchReadingLinks(
        current,
        researchReading.data,
        `${location.pathname}${location.search}`,
      ),
    ];
  }, [
    chapter.data,
    bookId,
    researchReading.data,
    location.pathname,
    location.search,
  ]);
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
      if (params.get("readerMode") !== "read" && (!target || failedTarget))
        setOriginal(true);
    }
  }, [chapter.data, params, requestedPage, target, failedTarget]);
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
    if (open) setNavigationOpen(false);
    const next = new URLSearchParams(params);
    next.set("tab", "reader");
    next.set("section", open ? "search" : "text");
    if (id) next.set("chapter", id);
    if (open) next.delete("readerInspector");
    setParams(next, { state: { preserveReadingPosition: true } });
  }
  function toggleRelated(open: boolean) {
    const next = new URLSearchParams(params);
    next.set("tab", "reader");
    next.set("section", "text");
    if (open) {
      next.set("readerInspector", "related");
      setNavigationOpen(false);
    } else next.delete("readerInspector");
    setParams(next, { state: { preserveReadingPosition: true } });
  }
  function readingMode(compare: boolean) {
    setOriginal(compare);
    const next = new URLSearchParams(params);
    next.set("readerMode", compare ? "compare" : "read");
    setParams(next, {
      replace: true,
      state: { preserveReadingPosition: true },
    });
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
      <ReaderNavigation
        bookId={bookId}
        open={navigationOpen}
        view={visibleNavigation}
        target={target}
        data={data}
        onClose={() => {
          setNavigationOpen(false);
          navigationToggle.current?.focus();
        }}
        onViewChange={setNavigationView}
        onSelect={go}
      />
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
          <div
            className="reader-mode-switch"
            role="group"
            aria-label="阅读模式"
          >
            <Button
              variant={original ? "ghost" : "outline"}
              aria-pressed={!original}
              onClick={() => readingMode(false)}
            >
              阅读文本
            </Button>
            <Button
              variant={original ? "outline" : "ghost"}
              aria-pressed={original}
              onClick={() => readingMode(true)}
            >
              对照原书
            </Button>
          </div>
          <Button
            variant={searchOpen ? "default" : "outline"}
            aria-expanded={searchOpen}
            aria-controls="reader-search-sidebar"
            onClick={() => toggleSearch(!searchOpen)}
          >
            <Search size={17} aria-hidden="true" />
            检索本书
          </Button>
          <Button
            variant={researchOpen ? "default" : "outline"}
            aria-expanded={researchOpen}
            onClick={() => toggleRelated(!researchOpen)}
          >
            相关史料
          </Button>
          <details className="reader-more">
            <summary>更多</summary>
            <div className="reader-more-panel">
              {chapter.data && (
                <div
                  className="reader-structure-control"
                  ref={structureControl}
                >
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
                      const menu = structureControl.current?.closest("details");
                      if (menu) menu.open = false;
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
          </details>
        </div>
        {failedTarget && (
          <p role="status" className="workspace-status-note">
            {targetSourceChanged
              ? "定位依据已更新，或此位置无法精确对应阅读版。请结合原图核对，未标记其他文字。"
              : "此位置暂不能精确对应阅读版。请结合原图核对，未标记其他文字。"}
          </p>
        )}
        {mappingFailures.some((value) => value !== readingFocus?.id) && (
          <p className="workspace-status-note">
            部分关联位置无法安全显示为链接，可从原书目录或“相关史料”继续查阅。
          </p>
        )}
        <div
          className="reader-body"
          data-search-open={searchOpen || researchOpen}
          data-reading-mode={original ? "compare" : "read"}
        >
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
                  viewId={`${bookId}:${id}:${readingFocus?.id ?? "page"}`}
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
          {researchOpen && (
            <ReadingResearchSidebar
              data={researchReading.data}
              returnTo={`${location.pathname}${location.search}`}
              onClose={() => toggleRelated(false)}
              error={researchReading.error}
              onRetry={() => void researchReading.refetch()}
            />
          )}
        </div>
      </div>
    </div>
  );
}
