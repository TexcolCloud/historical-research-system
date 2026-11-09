import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { research, researchKeys, isCardOnly, type ResearchRun } from "./api";

const runLabels: Record<string, string> = {
  ready: "研究任务等待处理",
  running: "研究任务正在处理",
  paused: "研究任务已暂停",
  pending: "研究任务待人工核验",
  completed: "研究任务已完成",
  failed: "研究任务因技术错误停止",
  technical_blocked: "研究任务因技术错误停止",
  cancelled: "研究任务已停止",
  superseded: "研究任务已由新版本替代",
};
const stageLabels: Record<string, string> = {
  author: "全文提取与史料制卡",
  enrich: "关联检索与补证",
  verify: "独立原文核验",
  repair: "定向修订",
  reverify: "再次独立核验",
};
const taskStateLabels: Record<string, string> = {
  ready: "等待处理",
  running: "处理中",
  leased: "处理中",
  completed: "本步已处理",
  failed: "技术错误",
  technical_blocked: "技术阻塞",
  paused: "已暂停",
};
export default function ResearchRunPanel({
  runId,
  initial,
  onOpenCard,
  legacy = false,
  compact = false,
}: {
  runId: string;
  initial?: ResearchRun;
  onOpenCard?: (id: string) => void;
  legacy?: boolean;
  compact?: boolean;
}) {
  const query = useQueryClient();
  const [candidateGroup, setCandidateGroup] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const key = researchKeys.run(runId);
  const run = useQuery({
    queryKey: key,
    initialData: initial,
    queryFn: ({ signal }) => research.run(runId, signal),
    staleTime: 0,
    refetchOnWindowFocus: false,
    refetchInterval: (current) =>
      current.state.data?.state === "running"
        ? 5000
        : current.state.data?.state === "ready"
          ? 10000
          : false,
  });
  const previous = useRef({
    state: run.data?.state,
    cards: run.data?.card_ids?.length ?? 0,
  });
  useEffect(() => {
    const completed =
      run.data?.state === "completed" && previous.current.state !== "completed";
    if (
      run.data &&
      ((previous.current.state === "running" && run.data.state !== "running") ||
        previous.current.cards !== (run.data.card_ids?.length ?? 0) ||
        completed)
    ) {
      void query.invalidateQueries({
        queryKey: researchKeys.book(run.data.book_id),
      });
      void query.invalidateQueries({
        queryKey: researchKeys.cards(run.data.book_id),
      });
    }
    if (completed)
      for (const kind of ["card", "entity", "catalogue", "search"]) {
        void query.invalidateQueries({ queryKey: ["research", kind] });
      }
    previous.current = {
      state: run.data?.state,
      cards: run.data?.card_ids?.length ?? 0,
    };
  }, [run.data, query]);
  const control = useMutation({
    onMutate: async () => {
      await query.cancelQueries({ queryKey: key });
    },
    mutationFn: (action: "pause" | "resume") => research.control(runId, action),
    onSuccess: (saved) => {
      query.setQueryData(key, saved);
      void query.invalidateQueries({
        queryKey: researchKeys.book(saved.book_id),
      });
      void query.invalidateQueries({
        queryKey: researchKeys.cards(saved.book_id),
      });
      void query.invalidateQueries({ queryKey: ["research", "question"] });
      void query.invalidateQueries({ queryKey: researchKeys.questions });
    },
  });
  if (run.isPending) return <p role="status">正在读取研究任务进展…</p>;
  if (run.isError)
    return (
      <p role="alert">
        {run.error.message}
        <Button onClick={() => void run.refetch()}>重新读取研究任务</Button>
      </p>
    );
  const data = run.data,
    counts = data.counts;
  const total = counts.articles_total ?? counts.articles ?? counts.total ?? 0;
  const completed = counts.articles_completed ?? counts.completed ?? 0;
  const cardIds = data.card_ids ?? [];
  const cardOnly = isCardOnly(data);
  const pending = counts.articles_pending ?? counts.pending ?? 0;
  const machineDone = Math.min(total, completed + pending);
  const tasks = data.tasks ?? [];
  const cardStages = [
    ["author", "制作"],
    ["verify", "独立审核"],
    ["repair", "定向修正"],
    ["reverify", "修正复核"],
  ] as const;
  const phase =
    [...cardStages]
      .reverse()
      .find(([stage]) =>
        tasks.some(
          (task) =>
            task.stage === stage && ["leased", "running"].includes(task.state),
        ),
      ) ??
    cardStages.find(([stage]) =>
      tasks.some((task) => task.stage === stage && task.state === "ready"),
    );
  const cardLabels: Record<string, string> = {
    ready: `史料卡待${phase?.[1] ?? "制作"}`,
    running: `史料卡正在${phase?.[1] ?? "制作"}`,
    paused: "史料卡制作已暂停",
    pending: "史料卡待人工核对",
    completed: "史料卡已完成",
    failed: "史料卡遇到技术故障",
    technical_blocked: "史料卡遇到技术故障",
    cancelled: "史料卡制作已停止",
    superseded: "史料卡已有后续版本",
  };
  const group = Math.min(
    candidateGroup,
    Math.max(0, Math.ceil(cardIds.length / 10) - 1),
  );
  const batchStart = group * 10;
  const title = cardOnly
    ? (cardLabels[data.state] ?? "制卡状态待确认")
    : legacy && data.state === "completed"
      ? "旧流程记录已完成"
      : (runLabels[data.state] ?? "研究任务状态待确认");
  const content = () => (
    <section
      className="research-run"
      data-state={data.state}
      aria-label={cardOnly ? "史料卡制作进展" : "旧流程任务进展"}
    >
      <div>
        <h2>{title}</h2>
        <p>
          {cardOnly
            ? "原文已入库；卡片独立制作和审核，阅读、原文检索及导出可继续。"
            : "旧流程任务 · 已有结果、笔记和人工决定继续保留"}
        </p>
        <p className="research-run-counts">
          {cardOnly ? (
            <>
              机器处理 {machineDone} / {total} · 已办结 {completed} · 待人工核对{" "}
              {pending} · 已保存卡片 {cardIds.length}
            </>
          ) : (
            <>
              {total > 0 && (
                <>
                  篇章 {completed} / {total} ·{" "}
                </>
              )}
              待人工核验篇章 {counts.articles_pending ?? counts.pending ?? 0} ·
              已保存卡片 {data.card_ids?.length ?? 0}
            </>
          )}
        </p>
        {total > 0 && (
          <progress
            aria-label={cardOnly ? "制卡机器处理进度" : "研究任务篇章进度"}
            value={cardOnly ? machineDone : completed}
            max={total}
          />
        )}
        {cardOnly && (
          <ol className="research-stage-progress" aria-label="制卡流程">
            {cardStages.map(([stage, label]) => {
              const rows = tasks.filter((task) => task.stage === stage);
              const active = rows.filter((task) =>
                ["leased", "running"].includes(task.state),
              ).length;
              const done = rows.filter(
                (task) => task.state === "completed",
              ).length;
              const human = rows.filter(
                (task) => task.state === "pending",
              ).length;
              const queued = rows.filter(
                (task) => task.state === "ready",
              ).length;
              const failed = rows.filter((task) =>
                ["failed", "technical_blocked"].includes(task.state),
              ).length;
              const parts = [
                active ? `处理中 ${active} 篇` : "",
                done ? `已处理 ${done} 篇` : "",
                human ? `已交人工 ${human} 篇` : "",
                queued ? `等待 ${queued} 篇` : "",
                failed ? `技术故障 ${failed} 篇` : "",
              ].filter(Boolean);
              return (
                <li key={stage}>
                  {label} ·{" "}
                  {parts.length
                    ? parts.join(" · ")
                    : ["repair", "reverify"].includes(stage)
                      ? "按需进行"
                      : "待处理"}
                </li>
              );
            })}
            <li>
              人工核对 · {pending ? `待处理 ${pending} 篇` : "有内容争议时进行"}
            </li>
            <li>
              完成 · 已办结 {completed} / {total} 篇
            </li>
          </ol>
        )}
        {!cardOnly &&
          (data.tasks ?? []).some((task) =>
            [
              "ready",
              "running",
              "leased",
              "failed",
              "technical_blocked",
            ].includes(task.state),
          ) && (
            <ul className="research-stage-progress" aria-label="当前研究阶段">
              {(data.tasks ?? [])
                .filter((task) =>
                  [
                    "ready",
                    "running",
                    "leased",
                    "failed",
                    "technical_blocked",
                  ].includes(task.state),
                )
                .slice(0, 20)
                .map((task) => (
                  <li key={task.task_id}>
                    {stageLabels[task.stage] || "研究处理"} ·{" "}
                    {taskStateLabels[task.state] || "等待确认"}
                  </li>
                ))}
            </ul>
          )}
        {data.state === "pending" && (
          <p>请打开待人工核验的卡片，对照原文作出决定。</p>
        )}
        {data.state === "pending" && onOpenCard && cardIds.length > 0 && (
          <div
            className="research-run-candidates"
            role="group"
            aria-label="本轮卡片快捷入口"
          >
            <p>
              本轮卡片 {batchStart + 1}–
              {Math.min(batchStart + 10, cardIds.length)} / {cardIds.length}
            </p>
            <div>
              {cardIds.slice(batchStart, batchStart + 10).map((id, index) => (
                <Button
                  key={id}
                  variant="outline"
                  onClick={() => onOpenCard(id)}
                >
                  查看本轮卡片 {batchStart + index + 1}
                </Button>
              ))}
            </div>
            {cardIds.length > 10 && (
              <div>
                <Button
                  variant="ghost"
                  disabled={group === 0}
                  onClick={() => setCandidateGroup(group - 1)}
                >
                  上一组卡片
                </Button>
                <Button
                  variant="ghost"
                  disabled={batchStart + 10 >= cardIds.length}
                  onClick={() => setCandidateGroup(group + 1)}
                >
                  下一组卡片
                </Button>
              </div>
            )}
          </div>
        )}
        {["failed", "technical_blocked"].includes(data.state) && (
          <p role="alert">
            任务遇到技术错误，已有卡片和证据已保留。继续任务会从断点重试。
          </p>
        )}
      </div>
      <div className="research-run-controls">
        {["ready", "running"].includes(data.state) && (
          <Button
            variant="outline"
            disabled={control.isPending}
            onClick={() => control.mutate("pause")}
          >
            {cardOnly ? "暂停制卡" : "暂停旧任务"}
          </Button>
        )}
        {["paused", "failed", "technical_blocked"].includes(data.state) && (
          <Button
            disabled={control.isPending}
            onClick={() => control.mutate("resume")}
          >
            {cardOnly ? "继续制卡" : "恢复旧任务（保留原流程）"}
          </Button>
        )}
        <Button
          variant="ghost"
          disabled={run.isFetching || control.isPending}
          onClick={() => void run.refetch()}
        >
          刷新进展
        </Button>
        {control.isPending && <p role="status">正在保存任务状态…</p>}
        {control.isError && <p role="alert">{control.error.message}</p>}
      </div>
    </section>
  );
  return compact ? (
    <details
      className="research-run-compact"
      open={expanded}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>
        <span>原文已入库</span>
        <strong data-state={data.state}>{title}</strong>
        <span>
          已办结 {completed} / {total} · 待人工 {pending}
        </span>
        <span>查看处理详情</span>
      </summary>
      {expanded && content()}
    </details>
  ) : (
    content()
  );
}
