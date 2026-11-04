import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client, type Book } from "@/client/api.ts";
import { booksKey } from "@/hooks/events.ts";
import { Button } from "@/components/ui/button.tsx";
import type { components } from "@/client/schema.d.ts";

type Entry = components["schemas"]["NavigationEntry"];
const labels: Record<string, string> = {
  matched: "已关联",
  matched_manual: "已确认位置",
  unlinked_manual: "已确认不关联",
  conflict: "需要确认位置",
  waiting_evidence: "等待系统核验",
  not_found: "本文件未找到目标",
  unresolved: "尚未关联",
};
const linked = (e: Entry) =>
  ["matched", "matched_manual", "unlinked_manual"].includes(e.status);
export default function NavigationPanel({
  runId,
  bookId,
  reviewOnly = false,
  entryId,
}: {
  runId: string;
  bookId: string;
  reviewOnly?: boolean;
  entryId?: string;
}) {
  const query = useQueryClient();
  const report = useQuery({
    queryKey: ["platform", "navigation", runId],
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/runs/{run_id}/navigation", {
        params: { path: { run_id: runId } },
        signal,
      });
      if (!data) throw new Error("目录尚未准备好，请稍后重试。");
      return data;
    },
  });
  const [selected, setSelected] = useState<string | null>(entryId ?? null),
    [filter, setFilter] = useState(reviewOnly ? "attention" : "all"),
    [page, setPage] = useState(""),
    [anchor, setAnchor] = useState("");
  const editor = useRef<HTMLElement>(null);
  useEffect(() => {
    if (selected) editor.current?.scrollIntoView?.({ block: "start" });
  }, [selected]);
  const decision = useMutation({
    mutationFn: async (target: boolean) => {
      const { data } = await client.POST(
        "/api/v2/runs/{run_id}/navigation/{entry_id}/decision",
        {
          params: { path: { run_id: runId, entry_id: selected! } },
          body: {
            expected_sha256: report.data!.sha256,
            target_page: target ? Number(page) : null,
            target_anchor: target ? anchor : "",
          },
        },
      );
      if (!data)
        throw new Error("目录或正文版本已更新，请重新查看候选位置后确认。");
      return data;
    },
    onSuccess: (data) => {
      query.setQueryData(["platform", "navigation", runId], data);
      setSelected(
        data.entries.find((e) => e.status === "conflict")?.id ?? null,
      );
      setPage("");
      setAnchor("");
      const navigation_pending_count = data.entries.filter(
        (e) => e.status === "conflict",
      ).length;
      query.setQueryData<Book[]>(booksKey, (books) =>
        books?.map((book) =>
          book.id === bookId ? { ...book, navigation_pending_count } : book,
        ),
      );
      query.setQueryData<Book>(["platform", "book", bookId], (book) =>
        book ? { ...book, navigation_pending_count } : book,
      );
      void query.invalidateQueries({ queryKey: booksKey });
      void query.invalidateQueries({ queryKey: ["platform", "book", bookId] });
    },
  });
  const entries = report.data?.entries ?? [],
    attention = entries.filter((e) => e.status === "conflict");
  const visible = entries.filter((e) =>
    filter === "attention"
      ? e.status === "conflict"
      : filter === "linked"
        ? linked(e)
        : filter === "waiting"
          ? !linked(e) && e.status !== "conflict"
          : true,
  );
  const target = report.data?.approved_pages?.find(
    (p) => p.page === Number(page),
  );
  function choose(e: Entry) {
    setSelected(e.id);
    setPage("");
    setAnchor("");
    decision.reset();
  }
  return (
    <section className="platform-navigation">
      <h2>{reviewOnly ? "确认目录位置" : "原书目录"}</h2>
      <p>
        {reviewOnly
          ? "对照目录与候选正文，选择位置后提交。此操作不改变正文审核结果。"
          : "浏览目录及关联原件；需要你确认的条目单独列出。"}
      </p>
      {report.isPending && <p role="status">正在读取目录…</p>}
      {report.isError && (
        <p role="alert">
          {report.error.message}
          <Button onClick={() => void report.refetch()}>重新读取</Button>
        </p>
      )}
      {report.data && (
        <>
          <div
            className="workspace-section-tabs"
            role="group"
            aria-label="目录状态"
          >
            <Button
              variant={filter === "attention" ? "default" : "outline"}
              onClick={() => {
                setFilter("attention");
                setSelected(null);
              }}
            >
              需你确认 · {attention.length}
            </Button>
            {!reviewOnly && (
              <>
                <Button
                  variant={filter === "linked" ? "default" : "outline"}
                  onClick={() => {
                    setFilter("linked");
                    setSelected(null);
                  }}
                >
                  已关联或确认 · {entries.filter(linked).length}
                </Button>
                <Button
                  variant={filter === "waiting" ? "default" : "outline"}
                  onClick={() => {
                    setFilter("waiting");
                    setSelected(null);
                  }}
                >
                  尚未关联 ·{" "}
                  {
                    entries.filter((e) => !linked(e) && e.status !== "conflict")
                      .length
                  }
                </Button>
                <Button
                  variant={filter === "all" ? "default" : "outline"}
                  onClick={() => {
                    setFilter("all");
                    setSelected(null);
                  }}
                >
                  全部目录 · {entries.length}
                </Button>
              </>
            )}
          </div>
          {!report.data.coverage_complete && (
            <p className="workspace-status-note">
              其余页面仍在核验，系统会继续补充目标。等待系统的条目无需人工处理。
            </p>
          )}
          {!visible.length && (
            <div className="workspace-empty">
              <h3>
                {filter === "attention"
                  ? "当前没有需要确认的目录位置"
                  : "暂无目录条目"}
              </h3>
              <p>
                {entries.length
                  ? "其他条目可在阅读资料中浏览。"
                  : "系统发现并核验目录后会在这里建立索引。"}
              </p>
              <a href={`#/books/${bookId}?tab=reader&section=directory`}>
                浏览原书目录
              </a>
            </div>
          )}
          <ol aria-label="原书目录" className="navigation-entry-list">
            {visible.map((e) => (
              <li key={e.id} data-selected={selected === e.id}>
                <header>
                  <div>
                    <strong>{e.title}</strong>
                    <p>
                      目录印刷页 {e.target_label}
                      {e.target_end_label ? `–${e.target_end_label}` : ""}
                    </p>
                  </div>
                  <span className="workspace-status-tag">
                    {labels[e.status] ?? "尚未关联"}
                  </span>
                </header>
                <p>{e.reason}</p>
                <div className="navigation-entry-links">
                  {e.target_chapter_id && e.target && (
                    <a
                      href={`#/books/${bookId}?tab=reader&chapter=${e.target_chapter_id}&physicalPage=${e.target.physical_page}&anchor=${encodeURIComponent(e.target.anchor)}`}
                    >
                      阅读正文 →
                    </a>
                  )}
                  <a href={e.source_image} target="_blank" rel="noreferrer">
                    目录原图（原件第 {e.source_page} 页）
                  </a>
                  {e.target && (
                    <a
                      href={e.target_image ?? e.target_url ?? undefined}
                      target="_blank"
                      rel="noreferrer"
                    >
                      正文原件第 {e.target.physical_page} 页
                    </a>
                  )}
                  {e.status === "conflict" && selected !== e.id && (
                    <Button variant="outline" onClick={() => choose(e)}>
                      核对目录位置
                    </Button>
                  )}
                </div>
                {selected === e.id && (
                  <section
                    ref={editor}
                    className="navigation-editor"
                    aria-label="目录位置确认"
                  >
                    <h3>对照原件，确认这一项目录</h3>
                    <div className="navigation-decision-actions">
                      <Button
                        disabled={!page || !anchor || decision.isPending}
                        onClick={() => decision.mutate(true)}
                      >
                        {decision.isPending ? "正在提交…" : "确认此位置"}
                      </Button>
                      <Button
                        variant="outline"
                        disabled={decision.isPending}
                        onClick={() => decision.mutate(false)}
                      >
                        确认不建立关联
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={decision.isPending}
                        onClick={() => setSelected(null)}
                      >
                        取消
                      </Button>
                    </div>
                    <div className="navigation-evidence-grid">
                      <figure>
                        <figcaption>
                          目录原文 · 原件第 {e.source_page} 页
                        </figcaption>
                        <blockquote>{e.source_anchor}</blockquote>
                        <img
                          src={e.source_image}
                          alt={`目录原件第 ${e.source_page} 页`}
                        />
                      </figure>
                      <div>
                        <h4>选择对应的正文位置</h4>
                        <p>原件页是PDF中的位置，印刷页是书上的页码。</p>
                        {e.candidates?.map((c, i) => (
                          <Button
                            key={`${c.physical_page}:${i}`}
                            variant={
                              Number(page) === c.physical_page &&
                              anchor === c.anchor
                                ? "default"
                                : "outline"
                            }
                            onClick={() => {
                              setPage(String(c.physical_page));
                              setAnchor(c.anchor);
                            }}
                            aria-pressed={
                              Number(page) === c.physical_page &&
                              anchor === c.anchor
                            }
                          >
                            选择候选 · 原件{c.physical_page}页 · 印刷页
                            {c.printed_label ?? "未知"} · {c.anchor}
                          </Button>
                        ))}
                        {!e.candidates?.length && (
                          <p>系统暂未给出候选，可从已核验页面中选择。</p>
                        )}
                        <details open={!e.candidates?.length}>
                          <summary>从其他已核验页面选择</summary>
                          <label>
                            目标原件页
                            <select
                              value={page}
                              onChange={(event) => {
                                setPage(event.target.value);
                                setAnchor("");
                              }}
                            >
                              <option value="">请选择页面</option>
                              {report.data!.approved_pages?.map((p) => (
                                <option key={p.page} value={p.page}>
                                  原件 {p.page} 页 · 印刷页{" "}
                                  {p.printed_label ?? "未知"}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label>
                            正文标题原文
                            <input
                              value={anchor}
                              onChange={(event) =>
                                setAnchor(event.target.value)
                              }
                              list="navigation-headings"
                            />
                          </label>
                          <datalist id="navigation-headings">
                            {target?.headings.map((h) => (
                              <option key={h} value={h} />
                            ))}
                          </datalist>
                          <div>
                            {target?.headings.map((h) => (
                              <Button
                                key={h}
                                variant="outline"
                                onClick={() => setAnchor(h)}
                              >
                                选择标题：{h}
                              </Button>
                            ))}
                          </div>
                        </details>
                        {target?.image && (
                          <figure>
                            <figcaption>
                              候选正文 · 原件第 {page} 页 · 印刷页{" "}
                              {target.printed_label ?? "未知"}
                            </figcaption>
                            <img
                              src={target.image}
                              alt={`候选正文原件第 ${page} 页`}
                            />
                            <a
                              href={target.image}
                              target="_blank"
                              rel="noreferrer"
                            >
                              打开候选正文原图
                            </a>
                          </figure>
                        )}
                      </div>
                    </div>
                    {decision.isError && (
                      <p role="alert">{decision.error.message}</p>
                    )}
                  </section>
                )}
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
