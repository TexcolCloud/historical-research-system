import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button.tsx";
import { reviewPageOptions } from "@/features/review/review-page-query.ts";
import type { components } from "@/client/schema.d.ts";
import type { NavigationChoice } from "./NavigationReview.tsx";
type Page = components["schemas"]["NavigationPage"];
const normalized = (value: string) =>
  value
    .normalize("NFKC")
    .toLocaleLowerCase()
    .replace(/[\s\p{P}\p{S}]/gu, "");

export function navigationOpening(text: string, anchor: string) {
  const at = text.indexOf(anchor);
  return anchor && at >= 0 && text.indexOf(anchor, at + anchor.length) < 0
    ? text.slice(at + anchor.length, at + anchor.length + 420).trim()
    : null;
}
export function Opening({ text }: { text: string }) {
  const date = text.match(
    /[（(]?[一二〇零三四五六七八九十百\d]{2,}年[^\n]{0,70}/,
  );
  const at = date?.index ?? -1;
  return (
    <p className="navigation-opening">
      {at >= 0 ? (
        <>
          {text.slice(0, at)}
          <mark>{date![0]}</mark>
          {text.slice(at + date![0].length)}
        </>
      ) : (
        text
      )}
    </p>
  );
}

export default function NavigationSearch({
  title,
  pages,
  pageCount,
  runId,
  busy,
  onChoose,
}: {
  title: string;
  pages: Page[];
  pageCount?: number | null;
  runId: string;
  busy: boolean;
  onChoose: (choice: NavigationChoice) => void;
}) {
  const [term, setTerm] = useState(title),
    [limit, setLimit] = useState(6);
  const terms = term.trim().split(/\s+/).map(normalized).filter(Boolean);
  const results = terms.length
    ? pages
        .flatMap((p) =>
          p.titles?.length
            ? p.titles.map((title) => ({
                page: title.physical_page,
                anchor: title.anchor,
                title: title.title ?? title.anchor,
                title_spans: title.title_spans,
                printed: title.printed_label ?? p.printed_label,
              }))
            : p.headings.map((anchor) => ({
                page: p.page,
                anchor,
                title: anchor,
                title_spans: undefined,
                printed: p.printed_label,
              })),
        )
        .filter((r) => terms.every((t) => normalized(r.title).includes(t)))
    : [];
  return (
    <section className="navigation-search" aria-label="查找其他正文">
      <h4>按标题查找</h4>
      <p>先按标题查找，打开原文节选后再对照原图确认。这里只查找已核验页面。</p>
      <label>
        查找正文标题
        <input
          type="search"
          value={term}
          disabled={busy}
          onChange={(e) => {
            setTerm(e.target.value);
            setLimit(6);
          }}
        />
      </label>
      <p role="status">
        {terms.length
          ? `找到 ${results.length} 个正文标题。`
          : "输入标题中的几个字即可查找。"}
      </p>
      {terms.length > 0 && !results.length && (
        <p>
          没有找到匹配标题。可缩短标题，或按 PDF
          页序定位正文；这不表示文件中没有该文章。
        </p>
      )}
      <ol className="navigation-search-results">
        {results.slice(0, limit).map((r) => (
          <SearchResult
            key={`${r.page}:${r.anchor}:${r.title}`}
            runId={runId}
            choice={r}
            printed={r.printed}
            busy={busy}
            onChoose={onChoose}
          />
        ))}
      </ol>
      {results.length > limit && (
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => setLimit(limit + 6)}
        >
          再显示 6 个标题
        </Button>
      )}
      <details className="navigation-page-picker">
        <summary>按 PDF 页序定位</summary>
        <PagePicker
          pages={pages}
          pageCount={pageCount}
          runId={runId}
          busy={busy}
          onChoose={onChoose}
        />
      </details>
    </section>
  );
}
function PagePicker({
  pages,
  pageCount,
  runId,
  busy,
  onChoose,
}: {
  pages: Page[];
  pageCount?: number | null;
  runId: string;
  busy: boolean;
  onChoose: (choice: NavigationChoice) => void;
}) {
  const [input, setInput] = useState(""),
    [viewed, setViewed] = useState(0);
  const approved = pages.find((p) => p.page === Number(input));
  const page = pages.find((p) => p.page === viewed);
  const result = useQuery({
    ...reviewPageOptions(runId, viewed),
    enabled: viewed > 0,
  });
  const text = result.data?.text ?? "";
  const possible = page?.headings.length
    ? page.headings
    : text
        .split(/\n+/)
        .map((s) => s.trim())
        .filter((s) => s && !/^!\[|^\[\^|^[\d\s]+$/.test(s))
        .map((s) => s.replace(/^#{1,6}\s+/, ""));
  const anchors = [...new Set(possible.map((s) => s.slice(0, 160)))].filter(
    (s) => s.length > 1 && navigationOpening(text, s) !== null,
  );
  return (
    <div>
      <p>
        输入 PDF
        中的页序，查看原图，再从该页已有文字中选择正文起点。仅已核验页面可以设置跳转。
      </p>
      <label>
        PDF 页序
        <input
          type="number"
          min="1"
          step="1"
          value={input}
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
        />
      </label>
      <Button
        variant="outline"
        disabled={busy || !approved}
        onClick={() => setViewed(approved!.page)}
      >
        查看这一页
      </Button>
      {input && !approved && (
        <p role="status">
          {!Number.isInteger(Number(input)) || Number(input) < 1
            ? "请输入从 1 开始的整数页序。"
            : pageCount != null && Number(input) > pageCount
              ? `页序超出范围，本 PDF 共 ${pageCount} 页。`
              : pageCount == null
                ? "当前已通过页面中没有该页。请先查看处理进展，确认 PDF 页数和核对状态。"
                : "该页尚未通过原图核对，暂不能设置跳转。可先查看阅读底稿或处理待核对内容。"}
        </p>
      )}
      {viewed > 0 && (
        <section aria-label={`PDF 第 ${viewed} 页正文起点`}>
          <h5>
            PDF 第 {viewed} 页 · 书内页码（系统识别）
            {page?.printed_label ?? "未知"}
          </h5>
          {result.isPending ? (
            <p>正在读取这一页…</p>
          ) : result.isError ? (
            <p role="alert">
              未能读取正文。
              <Button onClick={() => void result.refetch()}>
                重新读取这一页
              </Button>
            </p>
          ) : (
            <>
              {page?.image && (
                <a href={page.image} target="_blank" rel="noreferrer">
                  打开这一页原图
                </a>
              )}
              <p>
                {page?.headings.length
                  ? "从本页标题中选择正文起点。"
                  : "此页没有识别到标题，请从原文中选择正文起点。"}
              </p>
              <div className="navigation-starts">
                {anchors.map((anchor) => (
                  <Button
                    key={anchor}
                    variant="outline"
                    disabled={busy}
                    onClick={() => onChoose({ page: viewed, anchor })}
                  >
                    以此处为起点：{anchor}
                  </Button>
                ))}
              </div>
              {!anchors.length && (
                <p>本页暂未找到可唯一定位的文字起点，请查看原图并换页查找。</p>
              )}
            </>
          )}
        </section>
      )}
    </div>
  );
}
function SearchResult({
  runId,
  choice,
  printed,
  busy,
  onChoose,
}: {
  runId: string;
  choice: NavigationChoice;
  printed?: string | null;
  busy: boolean;
  onChoose: (choice: NavigationChoice) => void;
}) {
  const result = useQuery(reviewPageOptions(runId, choice.page));
  const opening = result.data
    ? navigationOpening(result.data.text, choice.anchor)
    : null;
  return (
    <li>
      <strong>{choice.title ?? choice.anchor}</strong>
      {(choice.title_spans?.length ?? 0) > 1 && (
        <p>
          跨页标题 · PDF 第{" "}
          {choice.title_spans!.map((span) => span.page).join("、")} 页
        </p>
      )}
      <p>
        PDF 第 {choice.page} 页 · 书内页码（系统识别）{printed ?? "未知"}
      </p>
      {result.isPending ? (
        <p>正在读取原文节选…</p>
      ) : result.isError ? (
        <p role="alert">
          原文暂时无法读取。
          <Button variant="outline" onClick={() => void result.refetch()}>
            重试读取
          </Button>
        </p>
      ) : opening !== null ? (
        <Opening text={opening || "（标题之后没有更多文字，请查看原图。）"} />
      ) : (
        <p>标题在当前正文中不能唯一定位，请按 PDF 页序重新选择起点。</p>
      )}
      <Button
        variant="outline"
        disabled={busy || result.isFetching || opening === null}
        onClick={() => onChoose(choice)}
      >
        对照这篇正文 · PDF 第 {choice.page} 页
      </Button>
    </li>
  );
}
