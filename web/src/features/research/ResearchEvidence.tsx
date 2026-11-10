import { Link, useLocation } from "react-router-dom";
import { useId } from "react";
import { ArrowUpRight } from "lucide-react";
import { readingHref } from "@/features/library/reader-location";
import type { ResearchEvidence } from "./api";

export default function ResearchEvidence({
  evidence,
  bookId,
  selected,
  historical = false,
  scope,
  offset = 0,
}: {
  evidence: ResearchEvidence[];
  bookId: string;
  selected?: string;
  historical?: boolean;
  scope?: string;
  offset?: number;
}) {
  const generatedScope = useId();
  const location = useLocation();
  const returnTo = `${location.pathname}${location.search}`;
  return (
    <section className="research-evidence" aria-label="原文证据">
      <h3>原文与出处</h3>
      {evidence.length ? (
        evidence.map((item, index) => (
          <article
            key={`${item.id}:${item.article_id}:${index}`}
            id={`research-evidence-${scope ?? generatedScope}-${index + offset}`}
            data-selected={selected === item.id}
          >
            <div className="research-evidence-heading">
              <strong>证据 {index + offset + 1}</strong>
              <span>PDF 第 {item.pages.join("、")} 页</span>
            </div>
            <blockquote>{item.quote}</blockquote>
            {!!item.footnotes?.length ? (
              <details className="research-footnotes">
                <summary>关联注释 · {item.footnotes.length} 项</summary>
                {item.footnotes.map((note) => (
                  <div key={note.id}>
                    {note.text && (
                      <p className="research-preserve">{note.text}</p>
                    )}
                    {note.reading_target && (
                      <Link
                        className="research-source-link"
                        to={readingHref(
                          item.book_id || bookId,
                          note.reading_target,
                          { returnTo },
                        )}
                      >
                        阅读注释 {note.label}
                      </Link>
                    )}
                  </div>
                ))}
              </details>
            ) : (
              !!item.footnote_ids.length && (
                <p className="research-provenance">
                  关联原文注释 {item.footnote_ids.length} 项，请打开原文核对。
                </p>
              )
            )}
            {item.reading_target ? (
              <Link
                className="research-source-link"
                to={readingHref(item.book_id || bookId, item.reading_target, {
                  returnTo,
                })}
              >
                {historical ? "阅读历史原文" : "阅读原文"} · PDF 第{" "}
                {item.pages.join("、")} 页
                <ArrowUpRight size={15} aria-hidden="true" />
              </Link>
            ) : (
              <p className="research-provenance">
                原文位置尚未可靠关联，不能自动定位。
              </p>
            )}
          </article>
        ))
      ) : (
        <p>尚无已保存的原文证据。</p>
      )}
    </section>
  );
}
