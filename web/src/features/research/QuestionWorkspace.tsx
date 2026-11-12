import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { readingHref } from "@/features/library/reader-location";
import {
  research,
  researchKeys,
  type ResearchQuestion,
  type ResearchSelection,
} from "./api";

const statusLabels: Record<string, string> = {
  open: "待研究",
  researching: "旧任务未结束",
  ready: "本轮自动处理已停止",
  resolved: "已整理",
  archived: "已归档",
};
const decisions: Record<ResearchSelection["decision"], string> = {
  saved: "已收集",
  adopted: "拟采用",
  excluded: "已排除",
};
function outdated(value: {
  source_current?: boolean;
  evidence?: Array<{ source_current?: boolean }>;
}) {
  return (
    value.source_current === false ||
    value.evidence?.some((item) => item.source_current === false)
  );
}
function exportText(question: ResearchQuestion) {
  return `# ${question.title}\n\n历史研究记录（只读）\n\n## 人工笔记\n\n${question.notes}\n\n## 完整保留记录\n\n\`\`\`json\n${JSON.stringify(question, null, 2)}\n\`\`\`\n`;
}

function HistoricalQuestion({ question }: { question: ResearchQuestion }) {
  return (
    <article className="research-question-detail">
      <header>
        <h2>{question.title}</h2>
        <span>{statusLabels[question.status] ?? question.status}</span>
      </header>
      <p className="research-provenance">
        记录版本 {question.version} · 题目版本 {question.definition_version} ·{" "}
        {question.book_ids.length
          ? `原范围 ${question.book_ids.length} 本书籍`
          : "原范围：全部已入库资料"}
      </p>
      <a
        className="research-source-link"
        download={`historical-question-${question.id}.md`}
        href={`data:text/markdown;charset=utf-8,${encodeURIComponent(exportText(question))}`}
      >
        导出历史记录 Markdown
      </a>
      <section>
        <h3>人工笔记</h3>
        <p className="research-preserve">{question.notes || "未记录笔记。"}</p>
      </section>
      <section>
        <h3>保留选材</h3>
        {!question.selections.length && <p>未保存选材。</p>}
        {question.selections.map((item) => (
          <article
            key={item.id}
            className="research-result"
            data-decision={item.decision}
          >
            <header>
              <strong>{item.title || "已保存材料"}</strong>
              <span>{decisions[item.decision]}</span>
            </header>
            {item.record_current === false && (
              <p className="workspace-status-note">
                此条卡片、人物或关系已有变化，保留的是当时的选材记录，请核对当前版本。
              </p>
            )}
            {outdated(item) && (
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
                查看实体与完整依据
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
                        returnTo: `/research?view=questions&question=${encodeURIComponent(question.id)}`,
                      })}
                    >
                      核对原文证据 {index + 1}
                    </Link>
                  )}
              </div>
            ))}
          </article>
        ))}
      </section>
      <section>
        <h3>历史查证结果</h3>
        {!question.findings.length && <p>未保存查证结果。</p>}
        {question.findings.map((finding, index) => (
          <article key={finding.id ?? index} className="research-result">
            {(finding.card_current === false || outdated(finding)) && (
              <p className="workspace-status-note">
                结论所依赖的卡片或原文已有新版本，当前保留历史查证结果。
              </p>
            )}
            <p className="research-provenance">
              旧模型结论 ·{" "}
              {finding.status === "answered"
                ? "已提供证据"
                : finding.status === "partial"
                  ? "部分有据"
                  : "尚待补证"}
              {finding.definition_version !== question.definition_version
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
      {!!question.runs.length && (
        <details>
          <summary>原任务记录</summary>
          {question.runs.map((run) => (
            <p key={run.run_id}>
              {run.run_id} · {run.state ?? "已保存"}
              {run.book_id && (
                <Link
                  to={`/books/${encodeURIComponent(run.book_id)}?tab=research&researchRun=${encodeURIComponent(run.run_id)}`}
                >
                  查看原任务
                </Link>
              )}
            </p>
          ))}
        </details>
      )}
    </article>
  );
}

export default function QuestionWorkspace() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("question") ?? "";
  const questions = useQuery({
    queryKey: researchKeys.questions,
    queryFn: ({ signal }) => research.questions(signal),
    staleTime: 30000,
  });
  const detail = useQuery({
    queryKey: researchKeys.question(selected),
    queryFn: ({ signal }) => research.question(selected, signal),
    enabled: Boolean(selected),
    staleTime: 30000,
  });
  return (
    <section className="research-questions">
      <header className="platform-heading">
        <div>
          <h1>历史研究记录</h1>
          <p>
            旧问题、选材、笔记和结论保留为只读记录，可核对原文及导出；新流程专注自动史料卡制作。
          </p>
        </div>
        <Link to="/library">返回书籍</Link>
      </header>
      <div className="research-question-layout">
        <nav aria-label="历史问题清单">
          {questions.isPending && <p role="status">正在读取历史记录…</p>}
          {questions.isError && (
            <p role="alert">
              {questions.error.message}
              <Button onClick={() => void questions.refetch()}>
                重新读取历史清单
              </Button>
            </p>
          )}
          {questions.data?.items.map((item) => (
            <button
              key={item.id}
              className="research-question-row"
              aria-pressed={item.id === selected}
              onClick={() => {
                const next = new URLSearchParams(params);
                next.set("question", item.id);
                setParams(next);
              }}
            >
              <strong>{item.title}</strong>
              <small>
                {statusLabels[item.status] ?? item.status} ·{" "}
                {item.selection_count ?? 0} 项材料
              </small>
            </button>
          ))}
          {questions.data?.items.length === 0 && <p>没有保留的旧研究问题。</p>}
        </nav>
        <div>
          {selected ? (
            detail.isPending ? (
              <p role="status">正在读取保留记录…</p>
            ) : detail.isError ? (
              <p role="alert">
                {detail.error.message}
                <Button onClick={() => void detail.refetch()}>
                  重新读取记录
                </Button>
              </p>
            ) : (
              detail.data && <HistoricalQuestion question={detail.data} />
            )
          ) : (
            <p>选择历史记录查看原有笔记和证据。</p>
          )}
        </div>
      </div>
    </section>
  );
}
