import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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

function entryCandidates(entry: Entry) {
  const candidates = entry.candidates ?? [];
  return entry.target
    ? [
        entry.target,
        ...candidates.filter(
          (candidate) =>
            candidate.physical_page !== entry.target!.physical_page ||
            candidate.anchor !== entry.target!.anchor,
        ),
      ]
    : candidates;
}

export default function NavigationReview({
  entry: latestEntry,
  report: latestReport,
  runId,
  busy,
  saving = false,
  approvalReady = true,
  onDecide,
  onCancel,
  error,
  onReload,
  reloading: fetchingReport = false,
}: {
  entry: Entry;
  report: Report;
  runId: string;
  busy: boolean;
  saving?: boolean;
  approvalReady?: boolean;
  onDecide: (choice: NavigationChoice | null, sha: string) => void;
  onCancel: () => void;
  error?: string;
  onReload?: () =>
    Report | undefined | void | Promise<Report | undefined | void>;
  reloading?: boolean;
}) {
  const query = useQueryClient();
  const [loadingEvidence, setLoadingEvidence] = useState(false);
  const [reloadFailure, setReloadFailure] = useState("");
  const reloading = fetchingReport || loadingEvidence;
  // Keep the displayed evidence and decision SHA together until an explicit reload.
  const [{ entry, report }, setSnapshot] = useState({
    entry: latestEntry,
    report: latestReport,
  });
  const candidates = entryCandidates(entry);
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
  const evidenceChanged = latestReport.sha256 !== report.sha256;
  useEffect(() => {
    if (!evidenceChanged || manual || !candidate) return;
    const sameSource = Boolean(
      entry.source_sha256 &&
      entry.source_image_sha256 &&
      entry.source_sha256 === latestEntry.source_sha256 &&
      entry.source_image_sha256 === latestEntry.source_image_sha256 &&
      entry.source_anchor === latestEntry.source_anchor &&
      entry.title === latestEntry.title,
    );
    const nextCandidates = entryCandidates(latestEntry);
    const nextIndex = nextCandidates.findIndex(
      (value) =>
        value.physical_page === candidate.physical_page &&
        value.anchor === candidate.anchor,
    );
    const next = nextCandidates[nextIndex];
    if (
      sameSource &&
      next &&
      "markdown_sha256" in candidate &&
      next.markdown_sha256 === candidate.markdown_sha256 &&
      next.image_sha256 === candidate.image_sha256 &&
      next.start === candidate.start
    ) {
      setIndex(nextIndex);
      setSnapshot({ entry: latestEntry, report: latestReport });
    }
  }, [evidenceChanged, entry, latestEntry, latestReport, manual, candidate]);
  async function reloadEvidence() {
    if (!onReload || reloading || busy) return;
    setLoadingEvidence(true);
    setReloadFailure("");
    try {
      const nextReport = await onReload();
      if (!nextReport) return;
      const nextEntry = nextReport.entries.find(
        (value) => value.id === entry.id,
      );
      if (!nextEntry) throw new Error("这条目录已更新，请返回目录重新选择。");
      const nextIndex = entryCandidates(nextEntry).findIndex(
        (value) =>
          value.physical_page === candidate?.physical_page &&
          value.anchor === candidate.anchor,
      );
      if (nextIndex >= 0 && !manual) setIndex(nextIndex);
      else if (candidate)
        setManual({ page: candidate.physical_page, anchor: candidate.anchor });
      setSnapshot({ entry: nextEntry, report: nextReport });
      await query.invalidateQueries(
        { queryKey: ["platform", "conversion-page", runId] },
        { throwOnError: true },
      );
    } catch (failure) {
      setReloadFailure(
        failure instanceof Error
          ? failure.message
          : "核对证据未能重新载入，请重试。",
      );
    } finally {
      setLoadingEvidence(false);
    }
  }
  const page = report.approved_pages?.find(
    (p) => p.page === candidate?.physical_page,
  );
  const mapping = report.page_mappings?.find(
    (value) => value.id === entry.group_id,
  );
  const evidence = useQuery({
    ...reviewPageOptions(runId, candidate?.physical_page ?? 0),
    enabled: Boolean(candidate),
  });
  const opening = candidate
    ? navigationOpening(evidence.data?.text ?? "", candidate.anchor)
    : null;
  const [searching, setSearching] = useState(!candidate);
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
    !error &&
    !reloadFailure &&
    !reloading &&
    !evidenceChanged &&
    approvalReady &&
    candidate &&
    !searching &&
    opening !== null &&
    !evidence.isFetching &&
    !evidence.isError;
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (
        !event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        event.repeat ||
        busy ||
        reloading ||
        evidenceChanged ||
        (event.target instanceof HTMLElement &&
          event.target.closest(
            "input, textarea, select, [contenteditable='true']",
          ))
      )
        return;
      if (event.key === "Enter" && canConfirm && candidate) {
        event.preventDefault();
        onDecide(
          { page: candidate.physical_page, anchor: candidate.anchor },
          report.sha256,
        );
      } else if (!searching && !manual && event.key === "ArrowRight") {
        event.preventDefault();
        setIndex((value) => Math.min(candidates.length - 1, value + 1));
      } else if (!searching && !manual && event.key === "ArrowLeft") {
        event.preventDefault();
        setIndex((value) => Math.max(0, value - 1));
      }
    };
    document.addEventListener("keydown", shortcut);
    return () => document.removeEventListener("keydown", shortcut);
  }, [
    busy,
    reloading,
    evidenceChanged,
    canConfirm,
    candidate,
    candidates.length,
    searching,
    manual,
    onDecide,
    report.sha256,
  ]);
  return (
    <section
      ref={section}
      className="navigation-review-workspace"
      aria-label="目录跳转核对"
    >
      <h3 className="sr-only">确认这条目录跳到哪里</h3>
      <div className="navigation-review-evidence">
        <div className="navigation-source-column">
          <header>
            <h4>目录中的这一条</h4>
            <div className="navigation-column-actions">
              <span>PDF 第 {entry.source_page} 页</span>
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={onCancel}
              >
                返回目录
              </Button>
            </div>
          </header>
          <div className="navigation-column-scroll">
            <details className="navigation-source-details">
              <summary>
                <strong>{entry.title}</strong>
                <span>书内页 {entry.target_label || "未标示"}</span>
              </summary>
              <blockquote>
                <mark>{entry.source_anchor}</mark>
              </blockquote>
            </details>
            <Original
              src={entry.source_image}
              label={`目录原图 · PDF 第 ${entry.source_page} 页`}
            />
          </div>
        </div>
        <div className="navigation-body-column" ref={candidatePanel}>
          <header>
            <h4 tabIndex={-1}>{searching ? "查找对应正文" : "待确认的正文"}</h4>
            <span>
              {candidate ? `PDF 第 ${candidate.physical_page} 页 · ` : ""}
              先预览，再确认
            </span>
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
                      onClick={() => void reloadEvidence()}
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
                {!manual && candidates.length > 1 && (
                  <ol
                    className="navigation-candidate-options"
                    aria-label="推荐正文候选"
                  >
                    {candidates.slice(0, 3).map((option, rank) => (
                      <CandidatePreview
                        key={`${option.physical_page}:${option.anchor}`}
                        candidate={option}
                        rank={rank}
                        runId={runId}
                        selected={index === rank}
                        disabled={busy || reloading || evidenceChanged}
                        onChoose={() => setIndex(rank)}
                      />
                    ))}
                  </ol>
                )}
                <div className="navigation-candidate-meta">
                  <h4>
                    <mark>{candidate?.anchor}</mark>
                  </h4>
                  <p>
                    书内页 {candidate?.printed_label ?? "未知"} ·
                    系统识别，请以原图为准
                  </p>
                  {!manual &&
                    Boolean(candidates[index]?.match_signals?.length) && (
                      <ul
                        className="navigation-match-signals"
                        aria-label="推荐依据"
                      >
                        {candidates[index].match_signals!.map((signal) => (
                          <li key={signal}>{signal}</li>
                        ))}
                      </ul>
                    )}
                  {mapping && (
                    <p>
                      本段已用 {mapping.anchor_count} 个可靠位置校准，其中{" "}
                      {mapping.manual_anchor_count}{" "}
                      个人工确认。请对照当前原图裁决。
                    </p>
                  )}
                </div>
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
        {(error || evidenceChanged || reloadFailure) && (
          <p role="alert" className="navigation-action-error">
            {reloadFailure ||
              error ||
              "目录或正文证据已更新，当前预览与选择已保留。请重新载入后核对。"}
            <Button
              variant="outline"
              disabled={reloading || !onReload}
              onClick={() => void reloadEvidence()}
            >
              {reloading ? "正在载入…" : "重新载入核对证据"}
            </Button>
          </p>
        )}
        <p>
          {approvalReady
            ? "预览不会保存。确认后仅更新目录跳转，正文与原件保留。"
            : "工作区范围未确认，暂不能提交目录决定。"}
          {approvalReady && (
            <small className="navigation-shortcuts">
              Alt + ← / → 切换候选 · Alt + Enter 确认当前正文
            </small>
          )}
        </p>
        <div>
          <Button
            variant="outline"
            disabled={
              busy ||
              reloading ||
              evidenceChanged ||
              Boolean(error || reloadFailure) ||
              !approvalReady
            }
            onClick={() => onDecide(null, report.sha256)}
          >
            {saving ? "正在保存…" : "保留目录文字，不设置跳转"}
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
              {saving
                ? "正在保存…"
                : `确认跳转到 PDF 第 ${candidate?.physical_page} 页`}
            </Button>
          )}
        </div>
      </footer>
    </section>
  );
}

function CandidatePreview({
  candidate,
  rank,
  runId,
  selected,
  disabled,
  onChoose,
}: {
  candidate: components["schemas"]["NavigationTarget"];
  rank: number;
  runId: string;
  selected: boolean;
  disabled: boolean;
  onChoose: () => void;
}) {
  const preview = useQuery({
    ...reviewPageOptions(runId, candidate.physical_page),
    staleTime: 30_000,
  });
  const opening =
    preview.data && navigationOpening(preview.data.text, candidate.anchor);
  return (
    <li data-selected={selected}>
      <button
        type="button"
        disabled={disabled}
        onClick={onChoose}
        aria-pressed={selected}
        aria-label={`查看候选 ${rank + 1} · PDF 第 ${candidate.physical_page} 页`}
      >
        <span>
          {rank === 0 ? "首选候选" : `候选 ${rank + 1}`} · PDF 第{" "}
          {candidate.physical_page} 页 · 书内页{" "}
          {candidate.printed_label ?? "未识别"}
        </span>
        <strong>{candidate.anchor}</strong>
      </button>
      {Boolean(candidate.match_signals?.length) && (
        <ul
          className="navigation-match-signals"
          aria-label={`候选 ${rank + 1} 推荐依据`}
        >
          {candidate.match_signals!.slice(0, 3).map((signal) => (
            <li key={signal}>{signal}</li>
          ))}
        </ul>
      )}
      {opening ? <Opening text={opening.slice(0, 120)} /> : null}
    </li>
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
