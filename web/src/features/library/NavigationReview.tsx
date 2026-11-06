import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, ExternalLink } from "lucide-react";
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
  approvalReady = true,
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
  approvalReady?: boolean;
  onDecide: (choice: NavigationChoice | null, sha: string) => void;
  onCancel: () => void;
  error?: string;
  onReload?: () => void;
  reloading?: boolean;
}) {
  // Keep the displayed evidence and decision SHA together until an explicit reload.
  const [{ entry, report }] = useState({
    entry: latestEntry,
    report: latestReport,
  });
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
  const opening = candidate
    ? navigationOpening(evidence.data?.text ?? "", candidate.anchor)
    : null;
  const [searching, setSearching] = useState(!candidate);
  const [mobileEvidence, setMobileEvidence] = useState("body");
  const section = useRef<HTMLElement>(null);
  const candidatePanel = useRef<HTMLDivElement>(null);
  const mounted = useRef(false);
  useEffect(() => {
    const measure = () => {
      const node = section.current;
      if (node)
        node.style.setProperty(
          "--navigation-height",
          `${Math.max(360, window.innerHeight - node.getBoundingClientRect().top - 16)}px`,
        );
    };
    measure();
    const frame = requestAnimationFrame(measure);
    void document.fonts?.ready.then(measure);
    window.addEventListener("resize", measure);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", measure);
    };
  }, []);
  useEffect(() => {
    if (mounted.current) {
      candidatePanel.current
        ?.querySelector<HTMLElement>(".navigation-column-scroll")
        ?.scrollTo?.({ top: 0 });
      candidatePanel.current
        ?.querySelector<HTMLElement>("h4")
        ?.focus({ preventScroll: true });
    }
    mounted.current = true;
  }, [searching, index, manual?.page, manual?.anchor]);
  const canConfirm =
    !busy &&
    approvalReady &&
    candidate &&
    !searching &&
    opening !== null &&
    !evidence.isFetching &&
    !evidence.isError;
  return (
    <section
      ref={section}
      className="navigation-review-workspace"
      aria-label="目录跳转核对"
    >
      <header className="navigation-review-heading">
        <div>
          <p className="navigation-eyebrow">目录与正文位置</p>
          <h3>确认这条目录跳到哪里</h3>
        </div>
        <Button variant="ghost" disabled={busy} onClick={onCancel}>
          返回目录
        </Button>
      </header>
      <div
        className="navigation-mobile-views"
        role="group"
        aria-label="目录证据视图"
      >
        <Button
          variant={mobileEvidence === "source" ? "default" : "outline"}
          aria-pressed={mobileEvidence === "source"}
          onClick={() => setMobileEvidence("source")}
        >
          目录证据
        </Button>
        <Button
          variant={mobileEvidence === "body" ? "default" : "outline"}
          aria-pressed={mobileEvidence === "body"}
          onClick={() => setMobileEvidence("body")}
        >
          正文候选
        </Button>
      </div>
      <div
        className="navigation-review-evidence"
        data-mobile-evidence={mobileEvidence}
      >
        <div className="navigation-source-column">
          <header>
            <h4>目录中的这一条</h4>
            <span>PDF 第 {entry.source_page} 页</span>
          </header>
          <div className="navigation-column-scroll">
            <blockquote>
              <mark>{entry.source_anchor}</mark>
            </blockquote>
            <dl className="navigation-comparison">
              <dt>标题</dt>
              <dd>{entry.title}</dd>
              <dt>书内页码</dt>
              <dd>{entry.target_label || "未标示"}</dd>
            </dl>
            <Original
              src={entry.source_image}
              label={`目录原图 · PDF 第 ${entry.source_page} 页`}
            />
          </div>
        </div>
        <div className="navigation-body-column" ref={candidatePanel}>
          <header>
            <h4 tabIndex={-1}>{searching ? "查找对应正文" : "待确认的正文"}</h4>
            <span>先预览，再确认</span>
          </header>
          <div className="navigation-column-scroll">
            {searching ? (
              <>
                {candidate && (
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => setSearching(false)}
                  >
                    返回刚才的正文
                  </Button>
                )}
                {!report.coverage_complete && onReload && (
                  <p className="navigation-refresh">
                    可选正文仅包含已通过原图核对的页面。
                    <Button
                      variant="ghost"
                      disabled={busy || reloading}
                      onClick={onReload}
                    >
                      更新可选正文
                    </Button>
                  </p>
                )}
                <NavigationSearch
                  title={entry.title}
                  pages={report.approved_pages ?? []}
                  pageCount={report.page_count}
                  runId={runId}
                  busy={busy}
                  onChoose={(choice) => {
                    setManual(choice);
                    setSearching(false);
                  }}
                />
              </>
            ) : (
              <>
                <div className="navigation-candidate-controls">
                  {!manual && candidates.length > 1 && (
                    <div className="navigation-candidate-switch">
                      <Button
                        variant="outline"
                        size="icon"
                        aria-label="上一个候选"
                        disabled={busy || index === 0}
                        onClick={() => setIndex(index - 1)}
                      >
                        <ArrowLeft size={17} />
                      </Button>
                      <span>
                        候选 {index + 1} / {candidates.length}
                      </span>
                      <Button
                        variant="outline"
                        size="icon"
                        aria-label="下一个候选"
                        disabled={busy || index === candidates.length - 1}
                        onClick={() => setIndex(index + 1)}
                      >
                        <ArrowRight size={17} />
                      </Button>
                    </div>
                  )}
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => setSearching(true)}
                  >
                    不是这篇，查找其他正文
                  </Button>
                </div>
                <dl className="navigation-comparison">
                  <dt>标题</dt>
                  <dd>
                    <mark>{candidate?.anchor}</mark>
                  </dd>
                  <dt>PDF 页序</dt>
                  <dd>第 {candidate?.physical_page} 页</dd>
                  <dt>书内页码</dt>
                  <dd>
                    {candidate?.printed_label ?? "未知"}
                    <small>系统识别，请以原图为准</small>
                  </dd>
                </dl>
                {candidate?.printed_label &&
                  entry.target_label &&
                  entry.target_label !== candidate.printed_label && (
                    <p className="navigation-difference">
                      书内页码不同：目录记载 {entry.target_label}
                      ；正文页码系统识别为 {candidate.printed_label}
                      。页码差异本身不代表正文不对应。
                    </p>
                  )}
                <h4>正文开头与日期</h4>
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
              </>
            )}
          </div>
        </div>
      </div>
      <footer
        className="navigation-decision-bar"
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
        <p>
          {approvalReady
            ? "预览不会保存。确认后仅更新目录跳转，正文与原件保留。"
            : "工作区范围未确认，暂不能提交目录决定。"}
        </p>
        <div>
          <Button
            variant="outline"
            disabled={busy || !approvalReady}
            onClick={() => onDecide(null, report.sha256)}
          >
            {busy ? "正在保存…" : "保留目录文字，不设置跳转"}
          </Button>
          {!searching && (
            <Button
              disabled={!canConfirm}
              onClick={() =>
                candidate &&
                onDecide(
                  { page: candidate.physical_page, anchor: candidate.anchor },
                  report.sha256,
                )
              }
            >
              {busy
                ? "正在保存…"
                : `确认跳转到 PDF 第 ${candidate?.physical_page} 页`}
            </Button>
          )}
        </div>
      </footer>
    </section>
  );
}

function Original({ src, label }: { src?: string | null; label: string }) {
  const [zoom, setZoom] = useState(100);
  if (!src)
    return (
      <p className="navigation-original-unavailable">
        当前原图不可用，请重新读取后核对。
      </p>
    );
  return (
    <figure className="navigation-original">
      <figcaption>
        {label}
        <a href={src} target="_blank" rel="noreferrer">
          打开完整原图
          <ExternalLink size={15} aria-hidden="true" />
        </a>
      </figcaption>
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
    </figure>
  );
}
