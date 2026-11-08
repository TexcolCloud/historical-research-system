import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { components } from "@/client/schema";
import { research, researchKeys } from "./api";
import {
  DateEditor,
  SourceEditor,
  emptyValue,
  emptyDate,
  type Draft,
} from "./CardFields";
import { CitationFields, CrossRelationFields } from "./CardCitationFields";
import { MentionFields, EventFields, RelationFields } from "./CardEntityFields";
import CardItemFields from "./CardItemFields";
import EvidenceOriginal from "./EvidenceOriginal";
import { useResearchPendingNavigation } from "./pending-navigation";

type S = components["schemas"];
type Metadata = S["DocumentMetadata"];
const metadataLabels: Record<
  Exclude<keyof Metadata, "title_origin">,
  string
> = {
  original_title: "原题",
  authors: "作者",
  recipients: "收受者",
  editors: "编者或整理者",
  publication: "出版信息",
  volume: "卷次",
  archive_id: "档号",
  formation_place: "成文地点",
  purpose: "写作目的",
  testimony: "亲历或转述性质",
};
function emptyMetadata(): Metadata {
  return {
    title_origin: "unknown",
    original_title: emptyValue(),
    authors: emptyValue(),
    recipients: emptyValue(),
    editors: emptyValue(),
    publication: emptyValue(),
    volume: emptyValue(),
    archive_id: emptyValue(),
    formation_place: emptyValue(),
    purpose: emptyValue(),
    testimony: emptyValue(),
  };
}
const lines = (value: string) =>
  value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);

function DraftForm({
  initial,
  version,
  cardId,
  onClose,
}: {
  initial: Draft;
  version: number;
  cardId: string;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(() => structuredClone(initial));
  const [baseVersion, setBaseVersion] = useState(version);
  const [validation, setValidation] = useState("");
  const query = useQueryClient();
  const pendingNavigation = useResearchPendingNavigation();
  const card = useQuery({
    queryKey: researchKeys.card(cardId),
    queryFn: ({ signal }) => research.card(cardId, signal),
    staleTime: 30000,
  });
  const save = useMutation({
    mutationFn: () => research.reviseCard(cardId, baseVersion, draft),
    onSuccess: (run) => {
      query.setQueryData(researchKeys.run(run.run_id), run);
      void query.invalidateQueries({ queryKey: ["research"] });
      void pendingNavigation.advance({ cardId });
    },
  });
  const latest = useMutation({
    mutationFn: () => research.editableCard(cardId),
  });
  const sameSourceCard =
    latest.data?.source_current &&
    !latest.data.superseded &&
    latest.data.card.id === initial.id &&
    latest.data.card.article_id === initial.article_id;
  const sourceUnavailable =
    (latest.data && !sameSourceCard) ||
    card.data?.source_current === false ||
    card.data?.superseded === true;
  function change<K extends keyof Draft>(field: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }
  const mentions = draft.mentions ?? [],
    events = draft.events ?? [],
    items = draft.items ?? [],
    relations = draft.relations ?? [],
    evidence = draft.evidence;
  return (
    <div className="research-editor-comparison">
      <form
        className="research-card-editor"
        onSubmit={(event) => {
          event.preventDefault();
          const missing = Object.entries(metadataLabels).flatMap(
            ([key, label]) => {
              const value =
                draft.metadata?.[key as keyof typeof metadataLabels];
              return value?.certainty === "unknown" && !value.note.trim()
                ? [label]
                : [];
            },
          );
          if (missing.length) {
            setValidation(`请说明以下未明字段的原因：${missing.join("、")}`);
            event.currentTarget
              .querySelectorAll("details")
              .forEach((item) => (item.open = true));
            return;
          }
          setValidation("");
          save.mutate();
        }}
      >
        <header>
          <h3>修订完整史料卡</h3>
          <p>
            修订将建立新版本并交独立核验。现有卡片、引文和人工决定继续保留。
          </p>
        </header>
        <fieldset disabled={save.isPending || save.isSuccess}>
          <label>
            卡片标题
            <Input
              value={draft.title}
              onChange={(event) => change("title", event.target.value)}
              required
            />
          </label>
          <label>
            内容提要
            <textarea
              aria-label="修订内容提要"
              value={draft.summary}
              onChange={(event) => change("summary", event.target.value)}
              rows={5}
              required
            />
          </label>
          <div className="research-editor-row">
            <label>
              文献类型
              <Input
                value={draft.document_type}
                onChange={(event) =>
                  change("document_type", event.target.value)
                }
              />
            </label>
            <label>
              史料层次
              <Input
                value={draft.source_layer}
                onChange={(event) => change("source_layer", event.target.value)}
              />
            </label>
          </div>
          <details>
            <summary>编目与形成信息</summary>
            {draft.metadata ? (
              <>
                <label>
                  标题来源
                  <select
                    value={draft.metadata.title_origin}
                    onChange={(event) =>
                      change("metadata", {
                        ...draft.metadata!,
                        title_origin: event.target
                          .value as Metadata["title_origin"],
                      })
                    }
                  >
                    <option value="source">原文题名</option>
                    <option value="supplied">已有书目信息</option>
                    <option value="system">系统拟题</option>
                    <option value="unknown">未明</option>
                  </select>
                </label>
                {(
                  Object.keys(metadataLabels) as Array<
                    keyof typeof metadataLabels
                  >
                ).map((key) => (
                  <SourceEditor
                    key={key}
                    label={metadataLabels[key]}
                    value={draft.metadata![key] ?? emptyValue()}
                    evidence={evidence}
                    onChange={(value) =>
                      change("metadata", { ...draft.metadata!, [key]: value })
                    }
                  />
                ))}
              </>
            ) : (
              <div>
                <p className="research-provenance">
                  旧卡未提取这些编目字段。需要补充时，请逐项记录有据内容或未明原因。
                </p>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => change("metadata", emptyMetadata())}
                >
                  补充编目信息
                </Button>
              </div>
            )}
          </details>
          <div className="research-editor-row">
            <DateEditor
              label="成文时间"
              value={draft.formation_date ?? emptyDate()}
              evidence={evidence}
              onChange={(value) => change("formation_date", value)}
            />
            <DateEditor
              label="事件时间"
              value={draft.event_date ?? emptyDate()}
              evidence={evidence}
              onChange={(value) => change("event_date", value)}
            />
          </div>
          <CardItemFields
            value={items}
            evidence={evidence}
            onChange={(value) => change("items", value)}
            removeBlocked={relations.flatMap((relation) => [
              relation.subject_id,
              relation.object_id,
            ])}
          />
          <CitationFields
            value={evidence}
            card={card.data}
            onChange={(value) => change("evidence", value)}
          />
          <MentionFields
            value={mentions}
            evidence={evidence}
            onChange={(value) => change("mentions", value)}
            removeBlocked={[
              ...events.flatMap((event) => event.participant_ids ?? []),
              ...relations.flatMap((relation) => [
                relation.subject_id,
                relation.object_id,
              ]),
              ...(draft.identity_links ?? []).map(
                (link) => link.local_mention_id,
              ),
            ]}
          />
          <EventFields
            value={events}
            mentions={mentions}
            evidence={evidence}
            onChange={(value) => change("events", value)}
            removeBlocked={[
              ...relations.flatMap((relation) => [
                relation.subject_id,
                relation.object_id,
              ]),
              ...(draft.identity_links ?? []).map(
                (link) => link.local_mention_id,
              ),
            ]}
          />
          <RelationFields
            value={relations}
            nodes={[
              ...mentions,
              ...events,
              ...items.map((item) => ({
                id: item.id,
                name: item.title || item.text.slice(0, 30),
              })),
            ]}
            evidence={evidence}
            onChange={(value) => change("relations", value)}
          />
          <CrossRelationFields
            value={draft.cross_relations ?? []}
            evidence={evidence}
            onChange={(value) => change("cross_relations", value)}
          />
          {(["tags", "limitations", "alternatives", "questions"] as const).map(
            (field) => (
              <label key={field}>
                {
                  {
                    tags: "主题词",
                    limitations: "使用边界",
                    alternatives: "备选解释",
                    questions: "待解决问题",
                  }[field]
                }
                （每行一项）
                <textarea
                  rows={3}
                  value={(draft[field] ?? []).join("\n")}
                  onChange={(event) => change(field, lines(event.target.value))}
                />
              </label>
            ),
          )}
          <p className="research-provenance">
            全文覆盖、检索计划和模型查证记录会随新修订再次独立核验。人物身份的跨卡合并与拆分请在人物详情中处理。
          </p>
          {validation && <p role="alert">{validation}</p>}
          <Button type="submit" disabled={Boolean(sourceUnavailable)}>
            提交修订核验
          </Button>
        </fieldset>
        <Button
          type="button"
          variant="ghost"
          disabled={save.isPending}
          onClick={onClose}
        >
          {save.isSuccess ? "返回卡片" : "取消修订"}
        </Button>
        {save.isPending && <p role="status">正在保存修订并安排独立核验…</p>}
        {save.isSuccess && (
          <p role="status">修订已提交独立核验，原有卡片和人工决定继续保留。</p>
        )}
        {pendingNavigation.notice && (
          <p role="status">{pendingNavigation.notice}</p>
        )}
        {save.isError && (
          <p role="alert">
            {save.error.message} 当前草稿仍保留。
            <Button
              type="button"
              variant="ghost"
              disabled={latest.isPending}
              onClick={() => latest.mutate()}
            >
              核对当前卡片
            </Button>
          </p>
        )}
        {latest.isPending && (
          <p role="status">正在读取最新可修订版本，当前草稿保留…</p>
        )}
        {latest.isError && (
          <p role="alert">{latest.error.message} 当前草稿仍保留。</p>
        )}
        {latest.data && (
          <section className="workspace-status-note" aria-label="修订版本对照">
            <h4>最新卡片 · 第 {latest.data.version} 版</h4>
            <p>{latest.data.card.title}</p>
            <p className="research-preserve">{latest.data.card.summary}</p>
            <Link
              className="research-source-link"
              target="_blank"
              to={`/research?card=${encodeURIComponent(cardId)}`}
            >
              另开页核对最新卡片与原件
            </Link>
            {sameSourceCard ? (
              <>
                <p>
                  核对最新内容后，可保留当前完整草稿，作为新修订重新提交独立核验。读取最新版本不会替换你的输入。
                </p>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setBaseVersion(latest.data.version);
                    save.reset();
                    latest.reset();
                  }}
                >
                  已核对最新版本，保留草稿继续修订
                </Button>
              </>
            ) : (
              <p>原文或研究版本已更新，当前草稿保留，不能提交到旧来源。</p>
            )}
          </section>
        )}
      </form>
      <aside className="research-source-pane">
        <EvidenceOriginal evidence={card.data?.evidence ?? []} />
      </aside>
    </div>
  );
}

export default function CardEditor({
  cardId,
  onClose,
}: {
  cardId: string;
  onClose: () => void;
}) {
  const [started, setStarted] = useState<S["EditableCardResponse"]>();
  const draft = useQuery({
    queryKey: ["research", "editable-card", cardId],
    queryFn: ({ signal }) => research.editableCard(cardId, signal),
    staleTime: 0,
    retry: false,
  });
  useEffect(() => {
    if (!started && draft.data?.source_current && !draft.data.superseded)
      setStarted(draft.data);
  }, [started, draft.data]);
  if (started)
    return (
      <DraftForm
        initial={started.card}
        version={started.version}
        cardId={cardId}
        onClose={onClose}
      />
    );
  if (draft.isPending) return <p role="status">正在读取可修订的完整卡片…</p>;
  if (draft.isError)
    return (
      <p role="alert">
        {draft.error.message}
        <Button onClick={() => void draft.refetch()}>重新读取修订内容</Button>
        <Button variant="ghost" onClick={onClose}>
          返回卡片
        </Button>
      </p>
    );
  if (!draft.data.source_current || draft.data.superseded)
    return (
      <p className="workspace-status-note">
        这份卡片的原文或研究版本已经更新，请先打开当前卡片。
        <Button onClick={onClose}>返回卡片</Button>
      </p>
    );
  return (
    <DraftForm
      initial={draft.data.card}
      version={draft.data.version}
      cardId={cardId}
      onClose={onClose}
    />
  );
}
