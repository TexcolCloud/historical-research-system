import { queryKeys } from "@/client/query-keys.ts";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/client/api.ts";
import { updateBooks } from "@/features/books/book-cache.ts";
import { Button } from "@/components/ui/button.tsx";
import NavigationReview, {
  type NavigationChoice,
} from "./NavigationReview.tsx";
import type { components } from "@/client/schema.d.ts";

type Entry = components["schemas"]["NavigationEntry"];
const labels: Record<string, string> = {
  matched: "可点击跳转",
  matched_manual: "已确认跳转",
  unlinked_manual: "已确认不设置跳转",
  conflict: "需要确认目录跳转",
  waiting_evidence: "等待系统核验",
  not_found: "未找到对应正文",
  unresolved: "尚未设置跳转",
};
const linked = (e: Entry) =>
  ["matched", "matched_manual", "unlinked_manual"].includes(e.status);
export default function NavigationPanel({
  runId,
  bookId,
  bookState,
  reviewOnly = false,
  entryId,
}: {
  runId: string;
  bookId: string;
  bookState?: string;
  reviewOnly?: boolean;
  entryId?: string;
}) {
  const query = useQueryClient();
  const report = useQuery({
    queryKey: queryKeys.navigation(runId),
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
    [message, setMessage] = useState(""),
    [restart, setRestart] = useState(0);
  const entries = report.data?.entries ?? [],
    attention = entries.filter((e) => e.status === "conflict");
  const incompleteMessage =
    bookState === "paused"
      ? "自动处理已暂停。请在处理进展中继续处理，新的目录项目将在核对后列出。"
      : bookState === "failed"
        ? "自动处理已停止。请在处理进展中从断点恢复，新的目录项目将在核对后列出。"
        : ["running", "processing", "queued"].includes(bookState ?? "")
          ? "目录尚未全部核对，新发现的项目会自动列出。"
          : "目录尚未全部核对，请在处理进展中查看剩余页面的状态。";
  const activeId = selected ?? (reviewOnly ? attention[0]?.id : null);
  const editor = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selected) editor.current?.scrollIntoView?.({ block: "start" });
  }, [selected]);
  const pendingDecision = useRef<{ signature: string; id: string } | null>(
    null,
  );
  const decision = useMutation({
    mutationFn: async ({
      choice,
      sha,
      id,
    }: {
      choice: NavigationChoice | null;
      sha: string;
      id: string;
    }) => {
      const signature = JSON.stringify([id, sha, choice]);
      if (pendingDecision.current?.signature !== signature)
        pendingDecision.current = { signature, id: crypto.randomUUID() };
      const { data, response } = await client.POST(
        "/api/v2/runs/{run_id}/navigation/{entry_id}/decision",
        {
          params: { path: { run_id: runId, entry_id: id } },
          body: {
            decision_id: pendingDecision.current.id,
            expected_sha256: sha,
            target_page: choice?.page ?? null,
            target_anchor: choice?.anchor ?? "",
          },
        },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "目录或正文已更新。你的选择尚未提交，请重新载入证据后核对。"
            : "未能保存跳转决定，请重试；本次选择仍保留。",
        );
      return data;
    },
    onSuccess: (data, { choice, id }) => {
      pendingDecision.current = null;
      const title = entries.find((e) => e.id === id)?.title ?? "这条目录";
      setMessage(
        choice
          ? `已确认：《${title}》 → PDF 第 ${choice.page} 页。`
          : `已确认：《${title}》保留目录文字，不设置跳转。`,
      );
      query.setQueryData(queryKeys.navigation(runId), data);
      setSelected(
        reviewOnly
          ? (data.entries.find((e) => e.status === "conflict")?.id ?? null)
          : null,
      );
      const navigation_pending_count = data.entries.filter(
        (e) => e.status === "conflict",
      ).length;
      updateBooks(
        query,
        (book) => book.id === bookId,
        (book) => ({ ...book, navigation_pending_count }),
      );
      // The next bridge event replaces these committed local counters after projection.
    },
  });
  const visible = reviewOnly
    ? entries.filter((e) => e.id === activeId)
    : entries.filter((e) =>
        filter === "attention"
          ? e.status === "conflict"
          : filter === "linked"
            ? linked(e)
            : filter === "waiting"
              ? !linked(e) && e.status !== "conflict"
              : true,
      );
  function choose(id: string) {
    setSelected(id);
    decision.reset();
    setRestart((v) => v + 1);
  }
  return (
    <section className="platform-navigation">
      <h2>{reviewOnly ? "核对目录跳转" : "原书目录"}</h2>
      {!reviewOnly && (
        <p>点击目录可阅读正文；跳转位置不对时，可在当前条目重新核对。</p>
      )}
      {message && (
        <p role="status" className="navigation-receipt">
          {message}
          {reviewOnly && attention.length > 0 ? " 已打开下一项目录。" : ""}
        </p>
      )}
      {report.isPending && <p role="status">正在读取目录…</p>}
      {report.isError && (
        <p role="alert">
          {report.error.message}
          <Button onClick={() => void report.refetch()}>重新读取目录</Button>
        </p>
      )}
      {report.data && (
        <>
          {reviewOnly ? (
            <p className="navigation-queue-status">
              {attention.length
                ? `还有 ${attention.length} 项目录需要核对。确认跳转只改变目录导航，正文与原件保留。`
                : "当前没有需要你确认的目录项目。"}
            </p>
          ) : (
            <div
              className="workspace-section-tabs"
              role="group"
              aria-label="目录状态"
            >
              {[
                ["attention", `需你核对 · ${attention.length}`],
                ["linked", `已设置或确认 · ${entries.filter(linked).length}`],
                [
                  "waiting",
                  `尚未设置 · ${entries.filter((e) => !linked(e) && e.status !== "conflict").length}`,
                ],
                ["all", `全部目录 · ${entries.length}`],
              ].map(([value, label]) => (
                <Button
                  key={value}
                  disabled={decision.isPending}
                  variant={filter === value ? "default" : "outline"}
                  onClick={() => {
                    setFilter(value);
                    setSelected(null);
                    decision.reset();
                  }}
                >
                  {label}
                </Button>
              ))}
            </div>
          )}
          {!visible.length && (
            <div className="workspace-empty">
              <h3>
                {reviewOnly && message
                  ? "当前目录核对完成"
                  : filter === "attention"
                    ? "当前没有需要核对的目录"
                    : "暂无目录条目"}
              </h3>
              <p>
                {report.data.coverage_complete
                  ? "已确认的跳转可以在原书目录中试用或修改。"
                  : incompleteMessage}
              </p>
              <a href={`#/books/${bookId}?tab=reader&section=directory`}>
                {message ? "试用目录跳转" : "浏览原书目录"}
              </a>
            </div>
          )}
          <ol aria-label="原书目录" className="navigation-entry-list">
            {visible.map((e) => (
              <li key={e.id} data-selected={activeId === e.id}>
                {activeId !== e.id && (
                  <>
                    <header>
                      <div>
                        <strong>{e.title}</strong>
                        <p>
                          书内页码 {e.target_label}
                          {e.target_end_label ? `–${e.target_end_label}` : ""}
                        </p>
                      </div>
                      <span className="workspace-status-tag">
                        {labels[e.status] ?? "尚未设置跳转"}
                      </span>
                    </header>
                    <div className="navigation-entry-links">
                      {e.target && (
                        <a
                          href={
                            e.target_chapter_id
                              ? `#/books/${bookId}?tab=reader&chapter=${e.target_chapter_id}&physicalPage=${e.target.physical_page}&anchor=${encodeURIComponent(e.target.anchor)}`
                              : (e.target_image ?? e.target_url ?? undefined)
                          }
                          target={e.target_chapter_id ? undefined : "_blank"}
                          rel="noreferrer"
                        >
                          {e.target_chapter_id
                            ? "阅读正文 →"
                            : `查看正文原图 · PDF 第 ${e.target.physical_page} 页`}
                        </a>
                      )}
                      <a href={e.source_image} target="_blank" rel="noreferrer">
                        目录原图 · PDF 第 {e.source_page} 页
                      </a>
                      {e.status !== "waiting_evidence" && (
                        <Button
                          variant="outline"
                          disabled={decision.isPending}
                          onClick={() => choose(e.id)}
                        >
                          {e.status === "conflict"
                            ? "确认目录跳转"
                            : "修改跳转"}
                        </Button>
                      )}
                    </div>
                  </>
                )}
                {activeId === e.id && (
                  <div ref={editor}>
                    <NavigationReview
                      key={`${e.id}:${restart}`}
                      entry={e}
                      report={report.data!}
                      runId={runId}
                      busy={decision.isPending}
                      error={decision.error?.message}
                      reloading={report.isFetching}
                      onReload={async () => {
                        const result = await report.refetch();
                        if (result.isSuccess) {
                          decision.reset();
                          setRestart((v) => v + 1);
                        }
                      }}
                      onDecide={(choice, sha) =>
                        decision.mutate({ choice, sha, id: e.id })
                      }
                      onCancel={() => {
                        if (reviewOnly)
                          window.location.hash = `/books/${bookId}?tab=reader&section=directory`;
                        else setSelected(null);
                      }}
                    />
                  </div>
                )}
              </li>
            ))}
          </ol>
          {!report.data.coverage_complete && visible.length > 0 && (
            <p className="navigation-queue-status">
              {incompleteMessage} 标为“等待系统核验”的条目暂无需人工处理。
            </p>
          )}
        </>
      )}
    </section>
  );
}
