import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import {
  skipToken,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { components } from "@/client/schema";
import { research, researchKeys, isCardOnly } from "./api";
import {
  DateEditor,
  DeferredFields,
  CardEditorReveal,
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
import { synchronizeCardCoverage } from "./card-coverage";
import CardDossierFields, { dossierValidation } from "./CardDossierFields";
import { useCardRevisionDraft } from "./useCardRevisionDraft";
import "./research-reading.css";

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
  cardOnly,
}: {
  initial: Draft;
  version: number;
  cardId: string;
  onClose: () => void;
  cardOnly: boolean;
}) {
  const runtime = useQuery<{ workspace_id?: string }>({
    queryKey: ["platform", "runtime"],
    queryFn: skipToken,
  });
  const editorId = useId();
  const local = useCardRevisionDraft(
    initial,
    version,
    cardId,
    runtime.data?.workspace_id,
  );
  const { draft, setDraft, baseVersion, setBaseVersion } = local;
  const [validation, setValidation] = useState("");
  const [validationTarget, setValidationTarget] = useState<string[]>([]);
  const [validationLabel, setValidationLabel] = useState("");
  const [reveal, setReveal] = useState<readonly string[]>([]);
  const query = useQueryClient();
  const pendingNavigation = useResearchPendingNavigation();
  const card = useQuery({
    queryKey: researchKeys.card(cardId),
    queryFn: ({ signal }) => research.card(cardId, signal),
    staleTime: 30000,
  });
  const save = useMutation({
    mutationFn: (submitted: Draft) =>
      research.reviseCard(cardId, baseVersion, submitted),
    onSuccess: (run) => {
      local.submitted();
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
  const sourceCards = cardOnly || isCardOnly(card.data);
  const dossier = "dossier" in draft ? draft.dossier : undefined;
  function change<K extends keyof Draft>(field: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }
  function reportValidation(message: string, keys: string[], label: string) {
    setValidation(message);
    setValidationTarget(keys);
    setValidationLabel(label);
    setReveal(keys);
  }
  const mentions = (draft.mentions ?? []).map((item) => ({
      ...item,
      locations: item.locations ?? [],
    })),
    events = draft.events ?? [],
    items = draft.items ?? [],
    relations = draft.relations ?? [],
    evidence = draft.evidence;
  return (
    <CardEditorReveal.Provider value={reveal}>
      <div className="research-editor-comparison">
        <form
          className="research-card-editor"
          onSubmit={(event) => {
            event.preventDefault();
            const dossierIssue =
              dossier && dossierValidation(dossier, evidence);
            if (dossierIssue) {
              reportValidation(
                dossierIssue.message,
                dossierIssue.keys,
                dossierIssue.label,
              );
              return;
            }
            const missing = Object.entries(metadataLabels).flatMap(
              ([key, label]) => {
                const value =
                  draft.metadata?.[key as keyof typeof metadataLabels];
                return !sourceCards &&
                  value?.certainty === "unknown" &&
                  !value.note.trim()
                  ? [{ key, label }]
                  : [];
              },
            );
            const missingLocations = sourceCards
              ? mentions.find((item) => !item.locations.length)
              : undefined;
            if (missing.length || missingLocations) {
              reportValidation(
                missing.length
                  ? `请说明以下未明字段的原因：${missing.map((item) => item.label).join("、")}`
                  : "请从本卡原文为新增实体选取称呼位置。",
                missing.length
                  ? ["metadata", `metadata:${missing[0].key}`]
                  : ["mentions", `mention:${missingLocations!.id}`],
                missing.length
                  ? missing[0].label
                  : `实体：${missingLocations!.name || "新增实体"}`,
              );
              return;
            }
            const prepared = sourceCards
              ? synchronizeCardCoverage(draft, initial)
              : { card: draft, missing: [] };
            if (
              sourceCards &&
              (!draft.coverage?.length || prepared.missing.length)
            ) {
              const missingObject = prepared.missing[0];
              let target: string[] = [];
              for (const [collection, prefix, label] of [
                ["mentions", "mention", "实体"],
                ["events", "event", "事件"],
                ["relations", "relation", "关系"],
              ] as const) {
                const object = (draft[collection] ?? []).find(
                  (item) =>
                    `${label}：${"name" in item ? item.name : item.predicate}` ===
                    missingObject,
                );
                if (object) {
                  target = [collection, `${prefix}:${object.id}`];
                  break;
                }
              }
              reportValidation(
                prepared.missing.length
                  ? `无法对应本篇原文位置：${prepared.missing.join("、")}。请补充本篇原文引用或重新选取称呼位置。`
                  : "全文覆盖位置不可用，请重新读取当前卡片后修订。",
                target,
                missingObject || "全文覆盖清单",
              );
              return;
            }
            setValidation("");
            setDraft(prepared.card);
            save.mutate(prepared.card);
          }}
        >
          <header>
            <h3>修订完整史料卡</h3>
            <p>
              修订将建立新版本并交独立核验。现有卡片、引文和人工决定继续保留。
            </p>
            {local.restored && (
              <p role="status">
                已恢复本机草稿 · 基于第 {baseVersion} 版，尚未提交核验。
              </p>
            )}
            <nav className="research-editor-groups" aria-label="修订内容分组">
              {[
                ["catalog", "编目与日期"],
                ["reading", "解读与边界"],
                ["evidence", "引文与原注"],
                ["entities", "实体与事件"],
              ].map(([id, label]) => (
                <Button
                  key={id}
                  type="button"
                  variant="ghost"
                  onClick={() =>
                    document
                      .getElementById(`${editorId}-${id}`)
                      ?.scrollIntoView?.({ block: "start" })
                  }
                >
                  {label}
                </Button>
              ))}
            </nav>
          </header>
          <fieldset disabled={save.isPending || save.isSuccess}>
            <section
              id={`${editorId}-catalog`}
              className="research-editor-section"
              aria-label="编目与日期"
            >
              <h4>编目与日期</h4>
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
                    onChange={(event) =>
                      change("source_layer", event.target.value)
                    }
                  />
                </label>
              </div>
              <DeferredFields summary="编目与形成信息" editorKey="metadata">
                {() => (
                  <>
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
                          <DeferredFields
                            key={key}
                            summary={metadataLabels[key]}
                            editorKey={`metadata:${key}`}
                          >
                            {() => (
                              <SourceEditor
                                label={metadataLabels[key]}
                                value={draft.metadata![key] ?? emptyValue()}
                                evidence={evidence}
                                onChange={(value) =>
                                  change("metadata", {
                                    ...draft.metadata!,
                                    [key]: value,
                                  })
                                }
                              />
                            )}
                          </DeferredFields>
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
                  </>
                )}
              </DeferredFields>
              <DeferredFields summary="时间信息">
                {() => (
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
                )}
              </DeferredFields>
            </section>
            <section
              id={`${editorId}-reading`}
              className="research-editor-section"
              aria-label="解读与边界"
            >
              <h4>解读与边界</h4>
              {dossier && (
                <CardDossierFields
                  value={dossier}
                  evidence={evidence}
                  onChange={(value) =>
                    setDraft((current) => ({ ...current, dossier: value }))
                  }
                />
              )}
              <CardItemFields
                value={items}
                evidence={evidence}
                cardOnly={sourceCards}
                onChange={(value) => change("items", value)}
                removeBlocked={relations.flatMap((relation) => [
                  relation.subject_id,
                  relation.object_id,
                ])}
              />
              {(["tags", "limitations", "alternatives", "questions"] as const)
                .filter(
                  (field) =>
                    !sourceCards ||
                    !["alternatives", "questions"].includes(field),
                )
                .map((field) => (
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
                      onChange={(event) =>
                        change(field, lines(event.target.value))
                      }
                    />
                  </label>
                ))}
            </section>
            <section
              id={`${editorId}-evidence`}
              className="research-editor-section"
              aria-label="引文与原注"
            >
              <h4>引文与原注</h4>
              <CitationFields
                value={evidence}
                card={card.data}
                onChange={(value) => change("evidence", value)}
              />
            </section>
            <section
              id={`${editorId}-entities`}
              className="research-editor-section"
              aria-label="实体与事件"
            >
              <h4>实体与事件</h4>
              <MentionFields
                value={mentions}
                evidence={evidence}
                cardOnly={sourceCards}
                articleId={draft.article_id}
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
                sourceEvidence={card.data?.evidence}
                retainedRelations={card.data?.cross_relations}
                onChange={(value) => change("cross_relations", value)}
              />
            </section>
            <p className="research-provenance">
              {sourceCards
                ? "修订将保留完整引文和原件依据，并重新独立审核。"
                : "全文覆盖、检索计划和模型查证记录会随新修订再次独立核验。"}
              实体身份的合并与拆分请在实体详情中处理。
            </p>
            {validation && (
              <div role="alert">
                <p>{validation}</p>
                <p>需要补充：{validationLabel}</p>
                {validationTarget.length > 0 && (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setReveal([...validationTarget])}
                  >
                    定位需要补充的位置
                  </Button>
                )}
              </div>
            )}
          </fieldset>
          <footer
            className="research-editor-actions"
            aria-label="修订保存与提交"
          >
            <p role="status">
              {save.isPending
                ? "正在提交独立核验…"
                : save.isSuccess
                  ? "修订已提交核验"
                  : local.storageError
                    ? "本机存储暂不可用，草稿保留在当前会话，刷新前请先提交或重试。"
                    : !local.persistent
                      ? "工作区尚未确认，草稿暂存当前会话，刷新前请先确认工作区。"
                      : local.dirty
                        ? local.stored
                          ? "本机草稿已保存 · 尚未提交核验"
                          : "正在保存本机草稿…"
                        : "尚未修改"}
            </p>
            <Button
              type="submit"
              disabled={
                Boolean(sourceUnavailable) ||
                local.versionConflict ||
                save.isPending ||
                save.isSuccess
              }
            >
              提交修订核验
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={save.isPending}
              onClick={() => {
                local.persist();
                onClose();
              }}
            >
              {save.isSuccess
                ? "返回卡片"
                : local.dirty
                  ? "保留草稿并返回"
                  : "取消修订"}
            </Button>
            {local.dirty && !save.isSuccess && (
              <Button
                type="button"
                variant="ghost"
                disabled={save.isPending}
                onClick={local.discard}
              >
                放弃本机草稿
              </Button>
            )}
          </footer>
          {save.isPending && <p role="status">正在保存修订并安排独立核验…</p>}
          {save.isSuccess && (
            <p role="status">
              修订已提交独立核验，原有卡片和人工决定继续保留。
            </p>
          )}
          {pendingNavigation.notice && (
            <p role="status">{pendingNavigation.notice}</p>
          )}
          {(save.isError || local.versionConflict) && (
            <p role="alert">
              {save.isError
                ? save.error.message
                : `本机草稿基于第 ${baseVersion} 版，当前卡片为第 ${version} 版。请先核对最新卡片。`}{" "}
              当前草稿仍保留。
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
            <section
              className="workspace-status-note"
              aria-label="修订版本对照"
            >
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
    </CardEditorReveal.Provider>
  );
}

function CardEditorSession({
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
        cardOnly={isCardOnly(started)}
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
      cardOnly={isCardOnly(draft.data)}
      version={draft.data.version}
      cardId={cardId}
      onClose={onClose}
    />
  );
}

export default function CardEditor(props: {
  cardId: string;
  onClose: () => void;
}) {
  return <CardEditorSession key={props.cardId} {...props} />;
}
