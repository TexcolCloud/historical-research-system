import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { client } from "@/client/api.ts";
import { Button } from "@/components/ui/button.tsx";
import type { components } from "@/client/schema.d.ts";

type Entry = components["schemas"]["NavigationEntry"];
const labels: Record<string, string> = {
  matched: "已关联",
  matched_manual: "已确认",
  conflict: "需确认页码",
  waiting_evidence: "等待目标核验",
  not_found: "本文件未找到目标",
  unresolved: "暂未关联",
};

export default function NavigationPanel({
  runId,
  bookId,
}: {
  runId: string;
  bookId: string;
}) {
  const report = useQuery({
    queryKey: ["platform", "navigation", runId],
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/runs/{run_id}/navigation", {
        params: { path: { run_id: runId } },
        signal,
      });
      if (!data) throw new Error("目录证据尚未准备好，请稍后重试。");
      return data;
    },
  });
  const [selected, setSelected] = useState<string | null>(null),
    [page, setPage] = useState(""),
    [anchor, setAnchor] = useState("");
  const entry = report.data?.entries.find((e) => e.id === selected);
  const decision = useMutation({
    mutationFn: async (target: boolean) => {
      const { data } = await client.POST(
        "/api/v2/runs/{run_id}/navigation/{entry_id}/decision",
        {
          params: { path: { run_id: runId, entry_id: entry!.id } },
          body: {
            expected_sha256: report.data!.sha256,
            target_page: target ? Number(page) : null,
            target_anchor: target ? anchor : "",
          },
        },
      );
      if (!data)
        throw new Error(
          "目录版本已变化或目标尚未通过核验，请刷新后重试。正文未被修改。",
        );
      return data;
    },
    onSuccess: () => {
      setSelected(null);
      void report.refetch();
    },
  });
  function choose(e: Entry) {
    setSelected(e.id);
    setPage(e.candidates?.[0]?.physical_page.toString() ?? "");
    setAnchor(e.candidates?.[0]?.anchor ?? "");
    decision.reset();
  }
  return (
    <section className="platform-navigation">
      <h2>原书目录导航</h2>
      <p>
        原件页是 PDF
        中的位置；印刷页是书上标注的页码。目录关联独立于文章整理，未关联条目仍可查看原目录。
      </p>
      {report.isPending && <p role="status">正在读取目录证据…</p>}
      {report.isError && (
        <p role="alert">
          {report.error.message}
          <Button onClick={() => void report.refetch()}>重试</Button>
        </p>
      )}
      {report.data && (
        <>
          <p>
            目录版本 {report.data.version} ·{" "}
            {report.data.coverage_complete
              ? "全部页面已核验"
              : "尚有页面待确认，目录目标会继续补充"}
          </p>
          {!report.data.entries.length && (
            <p>
              {report.data.state === "not_detected"
                ? "尚未形成原书目录索引。无目录材料可直接使用整理后的章节。"
                : "目录页面已发现，正在建立索引。"}
            </p>
          )}
          <nav aria-label="原书目录">
            <ol>
              {report.data.entries.map((e) => (
                <li
                  key={e.id}
                  style={{
                    marginLeft: `${Math.min(e.level - 1, 7) * 1.2}rem`,
                    marginBottom: "1rem",
                  }}
                >
                  <strong>{e.title}</strong> · 印刷页 {e.target_label}
                  {e.target_end_label ? `–${e.target_end_label}` : ""}{" "}
                  <span> · {labels[e.status] ?? e.status}</span>
                  <p>{e.reason}</p>
                  {e.target && (
                    <>
                      {e.target_chapter_id && (
                        <>
                          <a
                            href={`#/books/${bookId}?tab=reader&chapter=${e.target_chapter_id}&physicalPage=${e.target.physical_page}&anchor=${encodeURIComponent(e.target.anchor)}`}
                          >
                            阅读正文 →
                          </a>
                          {" · "}
                        </>
                      )}
                      <a
                        href={e.target_image ?? e.target_url ?? undefined}
                        target="_blank"
                        rel="noreferrer"
                      >
                        原件第 {e.target.physical_page} 页
                      </a>
                      {" · "}
                    </>
                  )}
                  <a href={e.source_image} target="_blank" rel="noreferrer">
                    目录原图（原件第 {e.source_page} 页）
                  </a>
                  {" · "}
                  <Button variant="outline" onClick={() => choose(e)}>
                    调整关联
                  </Button>
                </li>
              ))}
            </ol>
          </nav>
          {entry && (
            <div className="platform-review-content">
              <h3>{entry.title}：确认导航位置</h3>
              <blockquote>{entry.source_anchor}</blockquote>
              <p>只保存目录位置，不审批正文内容。</p>
              <label>
                目标原件页
                <select
                  value={page}
                  onChange={(e) => {
                    setPage(e.target.value);
                    setAnchor("");
                  }}
                >
                  <option value="">选择已核验页面</option>
                  {report.data.approved_pages?.map((p) => (
                    <option value={p.page} key={p.page}>
                      原件 {p.page} 页 · 印刷页 {p.printed_label ?? "未知"}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                正文标题原文
                <input
                  value={anchor}
                  onChange={(e) => setAnchor(e.target.value)}
                  list="navigation-headings"
                />
              </label>
              <datalist id="navigation-headings">
                {report.data.approved_pages
                  ?.find((p) => p.page === Number(page))
                  ?.headings.map((h) => (
                    <option value={h} key={h} />
                  ))}
              </datalist>
              {page && (
                <p>
                  <a
                    href={
                      report.data.approved_pages?.find(
                        (p) => p.page === Number(page),
                      )?.image ??
                      `/api/v2/runs/${runId}/artifacts/original.pdf#page=${Number(page)}`
                    }
                    target="_blank"
                    rel="noreferrer"
                  >
                    查看候选原件第 {page} 页
                  </a>{" "}
                  · 印刷页{" "}
                  {report.data.approved_pages?.find(
                    (p) => p.page === Number(page),
                  )?.printed_label ?? "未知"}
                </p>
              )}
              <Button
                disabled={!page || !anchor || decision.isPending}
                onClick={() => decision.mutate(true)}
              >
                确认此位置
              </Button>
              <Button
                variant="outline"
                disabled={decision.isPending}
                onClick={() => decision.mutate(false)}
              >
                暂不关联
              </Button>
              <Button variant="outline" onClick={() => setSelected(null)}>
                取消
              </Button>
              {decision.isError && <p role="alert">{decision.error.message}</p>}
            </div>
          )}
        </>
      )}
    </section>
  );
}
