import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { RenderedMarkdown } from "@/editor/RenderedMarkdown";
import { studies, StudyError } from "./api";
import type {
  StudyAnalysis,
  StudyAnalysisSuggestion,
  StudyRecord,
} from "./types";

export default function AnalysisSuggestion({
  workspace,
  suggestion,
  analysis,
  sourceCurrent,
  busy,
  onSource,
  onAdopted,
  onDisposition,
  onReload,
}: {
  workspace: string;
  suggestion: StudyAnalysisSuggestion;
  analysis: StudyAnalysis;
  sourceCurrent: boolean;
  busy: boolean;
  onSource: (ref: { material_id: string; evidence_id: string }) => void;
  onAdopted: () => void;
  onDisposition: (analysis: StudyAnalysis) => void;
  onReload: () => Promise<boolean>;
}) {
  const draftKey = workspace
    ? `hrs:study-adoption-draft:${workspace}:${analysis.id}:${suggestion.id}`
    : "";
  const [draft] = useState(() => readAdoptionDraft(draftKey));
  const [restored, setRestored] = useState(!!draft);
  const [kind, setKind] = useState<"note" | "argument" | "thought">(
    draft?.kind || "note",
  );
  const [adoptedId, setAdoptedId] = useState(
    suggestion.adopted_record_id || "",
  );
  const [editing, setEditing] = useState(!!draft);
  const [title, setTitle] = useState(draft?.title ?? suggestion.title);
  const [text, setText] = useState(draft?.text ?? suggestion.text);
  const [limitations, setLimitations] = useState(
    draft?.limitations ?? suggestion.limitations,
  );
  const [draftError, setDraftError] = useState("");
  const [reloading, setReloading] = useState(false);
  const [reloadError, setReloadError] = useState("");
  const [reloaded, setReloaded] = useState(false);
  const savedId = adoptedId || suggestion.adopted_record_id;
  function clearDraft() {
    if (draftKey)
      try {
        localStorage.removeItem(draftKey);
      } catch {
        /* The server receipt remains authoritative. */
      }
  }
  useEffect(() => {
    if (!draftKey || !editing || savedId) return;
    try {
      localStorage.setItem(
        draftKey,
        JSON.stringify({ kind, title, text, limitations }),
      );
    } catch {
      setDraftError(
        "本机个人解读草稿暂存失败，离开前请采纳保存或复制当前输入。",
      );
    }
  }, [draftKey, editing, savedId, kind, title, text, limitations]);
  const adopt = useMutation({
    mutationFn: () =>
      studies.adoptSuggestion(analysis.id, {
        expected_version: analysis.version,
        suggestion_id: suggestion.id,
        kind,
        title: editing ? title : suggestion.title,
        ...(editing ? { text, limitations } : {}),
      }),
    onSuccess: (record: StudyRecord) => {
      clearDraft();
      setAdoptedId(record.id);
      onAdopted();
    },
  });
  const disposition = useMutation({
    mutationFn: () =>
      studies.setSuggestionDisposition(analysis.id, suggestion.id, {
        expected_version: analysis.version,
        ignored: !suggestion.ignored,
      }),
    onSuccess: onDisposition,
  });
  const reloadRequired = [adopt.error, disposition.error].some(
    (error) => error instanceof StudyError && [0, 409].includes(error.status),
  );
  async function reloadAnalysis() {
    setReloading(true);
    setReloadError("");
    try {
      if (await onReload()) {
        adopt.reset();
        disposition.reset();
        setReloaded(true);
      } else setReloadError("重新读取未完成，当前个人解读仍保留。");
    } catch {
      setReloadError("重新读取未完成，当前个人解读仍保留。");
    } finally {
      setReloading(false);
    }
  }
  const currentSources = sourceCurrent && analysis.source_current !== false;
  const unavailableReason = suggestion.ignored
    ? "这条建议已忽略，恢复后才可采纳。"
    : !currentSources
      ? "所引来源已修订或已移出当前专题，旧建议与引用保留；请重新选材分析。"
      : !suggestion.verified
        ? "这条建议未通过独立引用核验，暂不可采纳。"
        : analysis.state !== "ready"
          ? "分析尚未完成，建议就绪后才可采纳。"
          : "";
  const usable = !unavailableReason;
  const pending = adopt.isPending || disposition.isPending || reloading;
  const label =
    kind === "note" ? "札记" : kind === "argument" ? "论据" : "观点";
  return (
    <article className="study-analysis-suggestion">
      <div className="study-detail-heading">
        <span className="study-badge">
          AI 建议 ·{" "}
          {suggestion.verified
            ? currentSources
              ? "引用已核对"
              : "保留版引用已核对"
            : "引用待核对"}
        </span>
        {savedId && <span role="status">已采纳为个人记录</span>}
        {suggestion.ignored && <span>已忽略 · 原建议保留</span>}
      </div>
      <h3>{suggestion.title}</h3>
      <RenderedMarkdown markdown={suggestion.text} />
      {suggestion.limitations && (
        <div className="study-limitations">
          <strong>使用限制</strong>
          <p className="study-preserve">{suggestion.limitations}</p>
        </div>
      )}
      <div className="study-source-links">
        {suggestion.evidence_refs.map((ref, index) => (
          <button
            key={`${ref.material_id}:${ref.evidence_id}`}
            onClick={() => onSource(ref)}
          >
            查看原文依据 {index + 1} · {ref.evidence_id}
          </button>
        ))}
      </div>
      {!!suggestion.issues?.length && (
        <details className="study-limitations">
          <summary>核对说明 · {suggestion.issues.length} 项</summary>
          <ul>
            {suggestion.issues.map((issue, index) => (
              <li key={index}>{issue}</li>
            ))}
          </ul>
        </details>
      )}
      {!!unavailableReason && (!savedId || !currentSources) && (
        <p className="study-muted">{unavailableReason}</p>
      )}
      {editing && !savedId && (
        <fieldset className="study-personal-adoption">
          <legend>修改个人解读后采纳</legend>
          <p>
            以下内容将另存为你的个人记录。机器原建议、核验结果与冻结引用保留。
          </p>
          {restored && <p role="status">已恢复本机个人解读草稿。</p>}
          <label>
            个人记录标题
            <input
              aria-label="个人记录标题"
              value={title}
              maxLength={300}
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <label>
            个人解读正文
            <textarea
              aria-label="个人解读正文"
              value={text}
              maxLength={60000}
              onChange={(event) => setText(event.target.value)}
            />
          </label>
          <label>
            个人记录使用限制
            <textarea
              aria-label="个人记录使用限制"
              value={limitations}
              maxLength={10000}
              onChange={(event) => setLimitations(event.target.value)}
            />
          </label>
        </fieldset>
      )}
      <div className="study-adoption-actions">
        <label>
          采纳为
          <select
            aria-label={`采纳类型 · ${suggestion.title}`}
            value={kind}
            disabled={!!savedId || pending}
            onChange={(event) => setKind(event.target.value as typeof kind)}
          >
            <option value="note">札记</option>
            <option value="argument">论据</option>
            <option value="thought">观点</option>
          </select>
        </label>
        <Button
          disabled={
            !usable ||
            busy ||
            !!savedId ||
            pending ||
            reloadRequired ||
            (editing && (!title.trim() || !text.trim() || !limitations.trim()))
          }
          onClick={() => adopt.mutate()}
        >
          {adopt.isPending
            ? "正在采纳…"
            : savedId
              ? "已采纳"
              : editing
                ? `采纳此修改为${label}`
                : `采纳为${label}`}
        </Button>
        {!savedId && (
          <Button
            variant="outline"
            disabled={!usable || pending || !workspace}
            onClick={() => {
              if (editing) {
                clearDraft();
                setRestored(false);
                setTitle(suggestion.title);
                setText(suggestion.text);
                setLimitations(suggestion.limitations);
              }
              setEditing(!editing);
            }}
          >
            {editing ? "取消个人修改" : "修改后采纳"}
          </Button>
        )}
        {!savedId && analysis.state === "ready" && (
          <Button
            variant="ghost"
            disabled={busy || pending || reloadRequired}
            onClick={() => disposition.mutate()}
          >
            {suggestion.ignored ? "恢复建议" : "忽略此建议"}
          </Button>
        )}
        {savedId && (
          <Link
            to={`/arguments?${new URLSearchParams({ topic: analysis.topic_id, kind, record: savedId })}`}
          >
            查看个人记录
          </Link>
        )}
      </div>
      {adopt.isError && <p role="alert">{adopt.error.message}</p>}
      {disposition.isError && <p role="alert">{disposition.error.message}</p>}
      {reloadRequired && (
        <Button
          variant="outline"
          disabled={busy || pending}
          onClick={() => void reloadAnalysis()}
        >
          {reloading ? "正在重新读取…" : "重新读取分析"}
        </Button>
      )}
      {reloaded && <p role="status">已重新读取分析，个人解读草稿仍保留。</p>}
      {reloadError && <p role="alert">{reloadError}</p>}
      {draftError && <p role="alert">{draftError}</p>}
    </article>
  );
}

type AdoptionDraft = {
  kind: "note" | "argument" | "thought";
  title: string;
  text: string;
  limitations: string;
};
function readAdoptionDraft(key: string): AdoptionDraft | null {
  if (!key) return null;
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    if (!value || !["note", "argument", "thought"].includes(value.kind))
      return null;
    if (
      typeof value.title !== "string" ||
      value.title.length > 300 ||
      typeof value.text !== "string" ||
      value.text.length > 60000 ||
      typeof value.limitations !== "string" ||
      value.limitations.length > 10000
    )
      return null;
    return value;
  } catch {
    return null;
  }
}
