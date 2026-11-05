import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button.tsx";
import { reviewPageOptions } from "@/features/review/review-page-query.ts";
import type { components } from "@/client/schema.d.ts";
import NavigationSearch, {
  navigationOpening,
  Opening,
} from "./NavigationSearch.tsx";

type Entry = components["schemas"]["NavigationEntry"];
type Report = components["schemas"]["NavigationReport"];
export type NavigationChoice = { page: number; anchor: string };

export default function NavigationReview({
  entry: latestEntry,
  report: latestReport,
  runId,
  busy,
  onDecide,
  onCancel,
  error,
  onReload,
  reloading = false,
}: {
  entry: Entry;
  report: Report;
  runId: string;
  busy: boolean;
  onDecide: (choice: NavigationChoice | null, sha: string) => void;
  onCancel: () => void;
  error?: string;
  onReload?: () => void;
  reloading?: boolean;
}) {
  // A preview is not a decision. Persist only the explicit final action.
  const [{ entry, report }] = useState({
    entry: latestEntry,
    report: latestReport,
  });
  const sha = report.sha256;
  const candidates = entry.target
    ? [
        entry.target,
        ...(entry.candidates ?? []).filter(
          (c) =>
            c.physical_page !== entry.target!.physical_page ||
            c.anchor !== entry.target!.anchor,
        ),
      ]
    : (entry.candidates ?? []);
  const [index, setIndex] = useState(0);
  const [manual, setManual] = useState<NavigationChoice | null>(null);
  const candidate = manual
    ? {
        physical_page: manual.page,
        anchor: manual.anchor,
        printed_label: report.approved_pages?.find(
          (p) => p.page === manual.page,
        )?.printed_label,
      }
    : candidates[index];
  const page = report.approved_pages?.find(
    (p) => p.page === candidate?.physical_page,
  );
  const evidence = useQuery({
    ...reviewPageOptions(runId, candidate?.physical_page ?? 0),
    enabled: Boolean(candidate),
  });
  const text = evidence.data?.text ?? "";
  const opening = candidate ? navigationOpening(text, candidate.anchor) : null;
  const [searching, setSearching] = useState(!candidate);
  const section = useRef<HTMLElement>(null),
    mounted = useRef(false);
  useEffect(() => {
    if (mounted.current) {
      section.current?.scrollIntoView?.({ block: "start" });
      section.current
        ?.querySelector<HTMLElement>("h3")
        ?.focus({ preventScroll: true });
    }
    mounted.current = true;
  }, [searching, index, manual?.page, manual?.anchor]);
  return (
    <section
      ref={section}
      className="navigation-editor navigation-guided"
      aria-label="目录跳转核对"
    >
      <h3 tabIndex={-1}>
        {searching
          ? "查找这条目录对应的正文"
          : "这条目录是否对应右侧这篇正文？"}
      </h3>
      <p className="navigation-task-help">
        {searching
          ? "目录原文保留在下方。找到正文后，先对照原图，再确认跳转。"
          : "对照标题、日期和正文开头，确认后这条目录将跳到右侧正文。书内页码是书上印的数字，PDF 页序用于定位原图。"}
      </p>
      {candidate && !searching && (
        <p className="navigation-difference">
          {entry.target_label === candidate.printed_label
            ? `目录与所选正文的书内页码均为 ${entry.target_label}，请核对标题、日期及开头是否对应。`
            : `书内页码不同：目录记载 ${entry.target_label}；正文页码系统识别为 ${candidate.printed_label ?? "未知"}。请查看原图核实，页码差异本身不代表正文不对应。`}
        </p>
      )}
      <div
        className="navigation-decision-actions"
        role="group"
        aria-label="目录裁决操作"
      >
        {error && (
          <p role="alert" className="navigation-action-error">
            {error}
            <Button variant="outline" disabled={reloading} onClick={onReload}>
              {reloading ? "正在载入…" : "重新载入核对证据"}
            </Button>
          </p>
        )}
        {!searching && (
          <>
            <Button
              disabled={
                busy ||
                !candidate ||
                opening === null ||
                evidence.isFetching ||
                evidence.isError
              }
              onClick={() =>
                onDecide(
                  { page: candidate.physical_page, anchor: candidate.anchor },
                  sha,
                )
              }
            >
              {busy ? "正在保存…" : "就是这篇，确认跳转"}
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setSearching(true)}
            >
              不是这篇，查找其他正文
            </Button>
          </>
        )}
        {searching && candidate && (
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => setSearching(false)}
          >
            返回刚才的正文
          </Button>
        )}
        <Button variant="ghost" disabled={busy} onClick={onCancel}>
          返回目录
        </Button>
      </div>
      {searching ? (
        <>
          {!report.coverage_complete && onReload && (
            <p className="navigation-refresh">
              正文仍在核验。需要查看刚完成的页面时，可
              <Button
                variant="outline"
                disabled={busy || reloading}
                onClick={onReload}
              >
                更新可选正文
              </Button>
              ，然后重新选择。
            </p>
          )}
          <blockquote className="navigation-search-source">
            <mark>{entry.source_anchor}</mark>
            <a href={entry.source_image} target="_blank" rel="noreferrer">
              查看目录原图 · PDF 第 {entry.source_page} 页
            </a>
          </blockquote>
          <NavigationSearch
            title={entry.title}
            pages={report.approved_pages ?? []}
            runId={runId}
            busy={busy}
            onChoose={(choice) => {
              setManual(choice);
              setSearching(false);
            }}
          />
          <section className="navigation-no-jump">
            <h4>不为这条目录设置跳转</h4>
            <p>这条目录仍会显示，但不能点击跳转；正文和原图均保留。</p>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => onDecide(null, sha)}
            >
              {busy ? "正在保存…" : "保留目录文字，不设置跳转"}
            </Button>
          </section>
        </>
      ) : (
        <>
          {!manual && candidates.length > 1 && (
            <div className="navigation-candidate-switch">
              <Button
                variant="outline"
                disabled={busy || index === 0}
                onClick={() => setIndex(index - 1)}
              >
                上一个候选
              </Button>
              <span>
                候选 {index + 1} / {candidates.length}
              </span>
              <Button
                variant="outline"
                disabled={busy || index === candidates.length - 1}
                onClick={() => setIndex(index + 1)}
              >
                下一个候选
              </Button>
            </div>
          )}
          <div className="navigation-evidence-grid">
            <div>
              <h4>目录中的这一条</h4>
              <blockquote>
                <mark>{entry.source_anchor}</mark>
              </blockquote>
              <p>
                书内页码 {entry.target_label} · PDF 第 {entry.source_page} 页
              </p>
              <Original
                src={entry.source_image}
                label={`目录原图 · PDF 第 ${entry.source_page} 页`}
              />
            </div>
            <div>
              <h4>待确认的正文</h4>
              <p>
                <mark>{candidate?.anchor}</mark>
              </p>
              <p>
                PDF 第 {candidate?.physical_page} 页 · 书内页码（系统识别）
                {candidate?.printed_label ?? "未知"}
              </p>
              {evidence.isPending ? (
                <p role="status">正在读取正文开头…</p>
              ) : evidence.isError ? (
                <p role="alert">
                  正文预览读取失败，暂不能确认。
                  <Button
                    variant="outline"
                    onClick={() => void evidence.refetch()}
                  >
                    重新读取正文
                  </Button>
                </p>
              ) : opening !== null ? (
                <Opening
                  text={opening || "（标题之后没有更多文字，请查看原图。）"}
                />
              ) : (
                <p role="alert">
                  当前正文中无法唯一定位此标题，请查找其他正文位置。
                </p>
              )}
              <Original
                src={page?.image ?? evidence.data?.image}
                label={`正文原图 · PDF 第 ${candidate?.physical_page} 页`}
              />
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function Original({ src, label }: { src?: string | null; label: string }) {
  const [zoom, setZoom] = useState(100);
  if (!src) return <p>当前原图不可用，请重新读取后核对。</p>;
  return (
    <figure className="navigation-original">
      <figcaption>{label}</figcaption>
      <label>
        原图放大{" "}
        <input
          type="range"
          min="100"
          max="200"
          step="50"
          aria-label={`${label}放大倍数`}
          value={zoom}
          onChange={(e) => setZoom(Number(e.target.value))}
        />
        {zoom}%
      </label>
      <div className="navigation-original-scroll">
        <img src={src} alt={label} style={{ width: `${zoom}%` }} />
      </div>
      <a href={src} target="_blank" rel="noreferrer">
        打开完整{label}
      </a>
    </figure>
  );
}
