import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { readingHref } from "@/features/library/reader-location";
import BookScope from "./BookScope";
import {
  research,
  researchKeys,
  type ResearchQuestion,
  type ResearchSelection,
} from "./api";

const statusLabels: Record<ResearchQuestion["status"], string> = {
  open: "待研究",
  researching: "正在查证",
  ready: "本轮自动处理已停止",
  resolved: "已整理",
  archived: "已归档",
};
const runStateLabels: Record<string, string> = {
  ready: "等待自动查证",
  running: "正在自动查证",
  pending: "需要人工核对",
  technical_blocked: "技术阻塞，需要恢复",
  failed: "技术错误，需要恢复",
  paused: "任务已暂停",
  completed: "查证已核验完成",
  cancelled: "任务已停止",
  superseded: "已有后续版本",
};
const manualStatusLabels = {
  open: "待研究",
  resolved: "已整理",
  archived: "已归档",
};
const selectionLabels: Record<ResearchSelection["decision"], string> = {
  saved: "已收集",
  adopted: "拟采用",
  excluded: "已排除",
};
function hasOutdatedSource(item: {
  source_current?: boolean;
  evidence?: Array<{ source_current?: boolean }>;
}) {
  return (
    item.source_current === false ||
    item.evidence?.some((span) => span.source_current === false) === true
  );
}

function QuestionDetail({ initial }: { initial: ResearchQuestion }) {
  const query = useQueryClient();
  const [title, setTitle] = useState(initial.title),
    [notes, setNotes] = useState(initial.notes),
    [books, setBooks] = useState(initial.book_ids),
    [status, setStatus] = useState(initial.status),
    [baseVersion, setBaseVersion] = useState(initial.version);
  const [selectionId, setSelectionId] = useState<string>(),
    [selectionNote, setSelectionNote] = useState("");
  const [base, setBase] = useState(initial);
  const detail = useQuery({
    queryKey: researchKeys.question(initial.id),
    queryFn: ({ signal }) => research.question(initial.id, signal),
    initialData: initial,
    refetchInterval: (state) =>
      state.state.data?.status === "researching" ? 5000 : false,
  });
  const current = detail.data;
  function retain(saved: ResearchQuestion) {
    setBaseVersion(saved.version);
    setBase(saved);
    query.setQueryData(researchKeys.question(saved.id), saved);
    void query.invalidateQueries({ queryKey: researchKeys.questions });
  }
  const save = useMutation({
    mutationFn: () =>
      research.updateQuestion(initial.id, baseVersion, {
        title: title.trim(),
        notes,
        book_ids: books,
        ...(status !== base.status ? { status } : {}),
      }),
    onSuccess: retain,
  });
  const investigate = useMutation({
    mutationFn: () => research.investigate(initial.id, baseVersion),
    onSuccess: (saved) => {
      retain(saved);
      setStatus(saved.status);
    },
  });
  const selection = useMutation({
    mutationFn: ({
      id,
      decision,
      note,
    }: {
      id: string;
      decision?: ResearchSelection["decision"];
      note?: string;
    }) =>
      research.updateSelection(initial.id, id, baseVersion, { decision, note }),
    onSuccess: (saved) => {
      retain(saved);
      setSelectionId(undefined);
    },
  });
  const busy = save.isPending || investigate.isPending || selection.isPending;
  const dirty =
    title !== base.title ||
    notes !== base.notes ||
    status !== base.status ||
    JSON.stringify(books) !== JSON.stringify(base.book_ids);
  useEffect(() => {
    if (
      !dirty &&
      (current.version !== baseVersion || current.status !== base.status)
    ) {
      setTitle(current.title);
      setNotes(current.notes);
      setBooks(current.book_ids);
      setStatus(current.status);
      setBaseVersion(current.version);
      setBase(current);
    }
  }, [current, baseVersion, base.status, dirty]);
  const error = save.error || selection.error || investigate.error;
  return (
    <article className="research-question-detail">
      <header>
        <h2>{current.title}</h2>
        <span className="research-status">{statusLabels[current.status]}</span>
      </header>
      <label>
        研究问题
        <Input
          aria-label="研究问题"
          value={title}
          maxLength={2000}
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <div className="research-question-scope">
        <span>
          {books.length
            ? `限定 ${books.length} 本书籍`
            : "查证范围：全部已入库书籍"}
        </span>
        <BookScope value={books} onApply={setBooks} />
      </div>
      <label>
        研究笔记
        <textarea
          aria-label="研究笔记"
          rows={6}
          value={notes}
          maxLength={60000}
          onChange={(event) => setNotes(event.target.value)}
        />
      </label>
      <p className="research-provenance">
        笔记记录你的判断；下面的模型结论和原文证据分别保留。
      </p>
      <label>
        整理状态
        <select
          aria-label="研究问题状态"
          value={status}
          onChange={(event) =>
            setStatus(event.target.value as ResearchQuestion["status"])
          }
        >
          {Object.entries(manualStatusLabels).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
          {(status === "researching" || status === "ready") && (
            <option value={status} disabled>
              {statusLabels[status]}（自动进展）
            </option>
          )}
        </select>
      </label>
      <div className="research-decision-actions">
        <Button
          disabled={busy || !title.trim() || !dirty}
          onClick={() => save.mutate()}
        >
          保存问题与笔记
        </Button>
        <Button
          variant="outline"
          disabled={
            busy ||
            dirty ||
            current.status === "researching" ||
            current.status === "archived"
          }
          onClick={() => investigate.mutate()}
        >
          继续查证
        </Button>
        <Button variant="ghost" onClick={() => void detail.refetch()}>
          刷新研究结果
        </Button>
      </div>
      {dirty && (
        <p className="research-provenance">问题修改保存后即可继续查证。</p>
      )}
      {busy && <p role="status">正在保存研究记录…</p>}
      {error && (
        <p role="alert">
          {error.message}
          <Button variant="ghost" onClick={() => void detail.refetch()}>
            重新读取问题
          </Button>
        </p>
      )}
      {current.version !== baseVersion && (
        <section className="workspace-status-note">
          <p>问题已有新版本。当前输入仍保留，请对照最新内容后继续。</p>
          <details>
            <summary>查看最新内容</summary>
            <p>{current.title}</p>
            <pre>{current.notes}</pre>
          </details>
          <Button
            variant="outline"
            onClick={() => {
              setTitle(current.title);
              setNotes(current.notes);
              setBooks(current.book_ids);
              setStatus(current.status);
              setBaseVersion(current.version);
              setBase(current);
              save.reset();
              selection.reset();
              investigate.reset();
            }}
          >
            用最新内容替换本地草稿
          </Button>
        </section>
      )}
      <section className="research-question-materials">
        <h3>选中的证据与材料 · {current.selections.length}</h3>
        {!current.selections.length && (
          <p>从检索结果或史料卡选择“保存到研究问题”，收集原文与依据。</p>
        )}
        {current.selections.map((item) => (
          <article
            key={item.id}
            className="research-result"
            data-decision={item.decision}
          >
            <header>
              <strong>{item.title || "已保存材料"}</strong>
              <span className="research-status">
                {selectionLabels[item.decision]}
              </span>
            </header>
            {item.record_current === false && (
              <p className="workspace-status-note">
                此条卡片、人物或关系已有变化，保留的是当时的选材记录，请核对当前版本。
              </p>
            )}
            {hasOutdatedSource(item) && (
              <p className="workspace-status-note">
                选材依据已有新版本，当前保留旧引文；重新核验后再引用。
              </p>
            )}
            {item.note && (
              <p className="research-preserve">选材说明：{item.note}</p>
            )}
            {item.kind === "card" && (
              <Link to={`/research?card=${encodeURIComponent(item.target_id)}`}>
                查看史料卡与完整依据
              </Link>
            )}
            {item.kind === "entity" && (
              <Link
                to={`/research?entity=${encodeURIComponent(item.target_id)}`}
              >
                查看人物事件与完整依据
              </Link>
            )}
            {(item.evidence ?? []).map((span, index) => (
              <div key={index}>
                {span.quote && <blockquote>{span.quote}</blockquote>}
                {span.source_current !== false &&
                  span.reading_target &&
                  span.book_id && (
                    <Link
                      to={readingHref(span.book_id, span.reading_target, {
                        returnTo: `/research?view=questions&question=${encodeURIComponent(current.id)}`,
                      })}
                    >
                      核对原文证据 {index + 1}
                    </Link>
                  )}
              </div>
            ))}
            <div className="research-decision-actions">
              <Button
                variant="outline"
                disabled={busy || item.decision === "adopted"}
                onClick={() =>
                  selection.mutate({ id: item.id, decision: "adopted" })
                }
              >
                拟采用
              </Button>
              <Button
                variant="ghost"
                disabled={busy || item.decision === "excluded"}
                onClick={() =>
                  selection.mutate({ id: item.id, decision: "excluded" })
                }
              >
                排除
              </Button>
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setSelectionId(item.id);
                  setSelectionNote(item.note);
                }}
              >
                编辑选材说明
              </Button>
            </div>
            {selectionId === item.id && (
              <div>
                <label>
                  选材说明
                  <textarea
                    aria-label="选材说明"
                    value={selectionNote}
                    onChange={(event) => setSelectionNote(event.target.value)}
                    rows={3}
                  />
                </label>
                <Button
                  disabled={busy}
                  onClick={() =>
                    selection.mutate({ id: item.id, note: selectionNote })
                  }
                >
                  保存选材说明
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => setSelectionId(undefined)}
                >
                  取消
                </Button>
              </div>
            )}
          </article>
        ))}
      </section>
      <section>
        <h3>查证结果</h3>
        {!current.findings.length && (
          <p>尚无已核验的模型查证结果。可以继续查证，原始选材和笔记会保留。</p>
        )}
        {current.findings.map((finding, index) => (
          <article className="research-result" key={finding.id ?? index}>
            {(finding.card_current === false || hasOutdatedSource(finding)) && (
              <p className="workspace-status-note">
                结论所依赖的卡片或原文已有新版本，当前保留历史查证结果。
              </p>
            )}
            <p className="research-provenance">
              模型查证 ·{" "}
              {finding.status === "answered"
                ? "已提供证据"
                : finding.status === "partial"
                  ? "部分有据"
                  : "尚待补证"}
              {finding.definition_version !== current.definition_version
                ? " · 对应早先题目版本"
                : ""}
            </p>
            <p className="research-preserve">{finding.text}</p>
            {!!finding.unresolved?.length && (
              <ul>
                {finding.unresolved.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            )}
            {finding.card_id && (
              <Link
                to={`/research?card=${encodeURIComponent(finding.card_id)}`}
              >
                核对结论对应史料卡
              </Link>
            )}
          </article>
        ))}
      </section>
      {!!current.runs.length && (
        <section>
          <h3>查证进展</h3>
          {current.runs.map((run) => (
            <p key={run.run_id}>
              {run.state
                ? (runStateLabels[run.state] ?? "任务进展待确认")
                : "任务已保存"}
              {run.book_id && (
                <Link
                  to={`/books/${encodeURIComponent(run.book_id)}?tab=research&researchRun=${encodeURIComponent(run.run_id)}`}
                >
                  查看任务与恢复操作
                </Link>
              )}
            </p>
          ))}
        </section>
      )}
    </article>
  );
}

export default function QuestionWorkspace() {
  const [params, setParams] = useSearchParams();
  const query = useQueryClient();
  const id = params.get("question") ?? "";
  const questions = useQuery({
    queryKey: researchKeys.questions,
    queryFn: ({ signal }) => research.questions(signal),
    staleTime: 30000,
  });
  const detail = useQuery({
    queryKey: researchKeys.question(id),
    queryFn: ({ signal }) => research.question(id, signal),
    enabled: Boolean(id),
  });
  const [creating, setCreating] = useState(false),
    [title, setTitle] = useState(""),
    [books, setBooks] = useState<string[]>([]);
  function open(id: string) {
    const next = new URLSearchParams(params);
    next.set("view", "questions");
    next.set("question", id);
    next.delete("card");
    next.delete("entity");
    setParams(next);
  }
  const create = useMutation({
    mutationFn: () => research.createQuestion(title.trim(), books),
    onSuccess: (saved) => {
      query.setQueryData(researchKeys.question(saved.id), saved);
      void query.invalidateQueries({ queryKey: researchKeys.questions });
      setCreating(false);
      setTitle("");
      open(saved.id);
    },
  });
  return (
    <section className="research-questions">
      <header className="platform-heading">
        <div>
          <h1>研究问题</h1>
          <p>围绕问题收集证据，分别保留原文、查证结果和自己的判断。</p>
        </div>
        <Button onClick={() => setCreating(true)}>新建研究问题</Button>
      </header>
      {creating && (
        <form
          className="research-question-create"
          onSubmit={(event) => {
            event.preventDefault();
            if (title.trim()) create.mutate();
          }}
        >
          <label>
            问题
            <Input
              aria-label="新研究问题"
              value={title}
              maxLength={2000}
              onChange={(event) => setTitle(event.target.value)}
              autoFocus
            />
          </label>
          <BookScope value={books} onApply={setBooks} />
          <Button type="submit" disabled={create.isPending || !title.trim()}>
            创建问题
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => setCreating(false)}
          >
            取消
          </Button>
          {create.isError && <p role="alert">{create.error.message}</p>}
        </form>
      )}
      <div className="research-question-layout">
        <nav aria-label="研究问题清单">
          {questions.isPending && <p role="status">正在读取研究问题…</p>}
          {questions.isError && (
            <p role="alert">
              {questions.error.message}
              <Button onClick={() => void questions.refetch()}>
                重新读取问题清单
              </Button>
            </p>
          )}
          {questions.data?.items.map((item) => (
            <button
              key={item.id}
              aria-pressed={id === item.id}
              onClick={() => open(item.id)}
            >
              <strong>{item.title}</strong>
              <small>
                {statusLabels[item.status]}
                {typeof item.selection_count === "number" &&
                  ` · ${item.selection_count} 项材料`}
              </small>
            </button>
          ))}
          {questions.data?.items.length === 0 && (
            <p>尚无研究问题。新建问题后即可整理选材与笔记。</p>
          )}
        </nav>
        {id ? (
          detail.isError ? (
            <p role="alert">
              {detail.error.message}
              <Button onClick={() => void detail.refetch()}>
                重新读取问题
              </Button>
            </p>
          ) : detail.data ? (
            <QuestionDetail key={id} initial={detail.data} />
          ) : (
            <p role="status">正在读取问题记录…</p>
          )
        ) : (
          <p className="research-empty">
            选择研究问题，或新建一个问题开始整理。
          </p>
        )}
      </div>
    </section>
  );
}
