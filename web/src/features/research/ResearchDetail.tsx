import { lazy, Suspense, useId, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import Evidence from "./ResearchEvidence";
import EvidenceOriginal from "./EvidenceOriginal";
import QuestionPicker from "./QuestionPicker";
import CardEditor from "./CardEditor";
import IdentityEditor from "./IdentityEditor";
import CrossCardRelations from "./CrossCardRelations";
import EventNature from "./EventNature";
import EntityTimeline from "./EntityTimeline";
import DerivedCardRelations from "./DerivedCardRelations";
import { relationLabel } from "./relation-labels";
import { useResearchPendingNavigation } from "./pending-navigation";
const ResearchGraph = lazy(() => import("./ResearchGraph"));
import {
  research,
  researchKeys,
  ResearchError,
  type ResearchCard,
} from "./api";

export const cardStateLabel = (state: string) =>
  ({
    verified: "原文核验通过",
    pending: "待人工核验",
    human_accepted: "已人工确认",
    excluded: "已排除",
  })[state] ?? state;
const certaintyLabels: Record<string, string> = {
  stated: "原文明确记载",
  inferred: "推断",
  unknown: "未明",
  disputed: "有争议",
};
const dateLabel = (date?: ResearchCard["formation_date"] | null) =>
  date?.text
    ? `${date.text}（${certaintyLabels[date.certainty] ?? date.certainty}）`
    : "";
const kindLabels: Record<string, string> = {
  section: "内容层次",
  evidence: "论据",
  argument: "论证",
  issue: "问题",
  paper_use: "论文用途",
  person: "人物",
  event: "事件",
  organization: "机构",
  place: "地点",
};

function SourceNotice({
  bookId,
  stale,
  revisionPending,
  superseded,
}: {
  bookId: string;
  stale: boolean;
  revisionPending: boolean;
  superseded: boolean;
}) {
  if (!stale && !revisionPending && !superseded) return null;
  return (
    <div className="research-source-notice" role="note">
      <strong>
        {stale
          ? "原文已换版 · 卡片待更新"
          : superseded
            ? "研究卡已修订 · 历史版本"
            : "原文修订核验中"}
      </strong>
      <p>
        {stale
          ? "这份研究记录对应旧版原文，仅供历史核查。请使用当前原文重新核验后引用。"
          : superseded
            ? "这份研究记录已由新修订替代，原有证据与人工决定继续保留。"
            : "当前仍展示此前已发布原文的研究结果，新原文发布后需更新核验。"}
      </p>
      <Link
        className="research-source-link"
        to={`/books/${encodeURIComponent(bookId)}?tab=research`}
      >
        查看本书当前研究进展
      </Link>
    </div>
  );
}

function CardContent({ card }: { card: ResearchCard }) {
  const evidenceScope = useId();
  const pendingNavigation = useResearchPendingNavigation();
  const query = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<string>();
  const [editing, setEditing] = useState(false);
  const [fullEditing, setFullEditing] = useState(false);
  const [text, setText] = useState(card.summary);
  const decision = useMutation({
    onMutate: async () => {
      await query.cancelQueries({ queryKey: researchKeys.card(card.id) });
    },
    mutationFn: ({
      action,
      text,
    }: {
      action: "accept" | "edit" | "reject";
      text?: string;
    }) => research.decision(card.id, action, card.version, text),
    onSuccess: (saved) => {
      query.setQueryData(researchKeys.card(saved.id), saved);
      setEditing(false);
      void query.invalidateQueries({
        queryKey: researchKeys.book(saved.book_id),
      });
      void query.invalidateQueries({
        queryKey: researchKeys.cards(saved.book_id),
      });
      void query.invalidateQueries({
        queryKey: researchKeys.run(saved.run_id),
      });
      void query.invalidateQueries({ queryKey: ["research", "search"] });
      void query.invalidateQueries({ queryKey: ["research", "entity"] });
      void pendingNavigation.advance({ cardId: saved.id });
    },
  });
  function focusEvidence(identity: string) {
    setSelected(identity);
    document
      .getElementById(
        `research-evidence-${evidenceScope}-${card.evidence.findIndex((item) => item.id === identity)}`,
      )
      ?.scrollIntoView({ block: "nearest" });
  }
  function refs(identities: string[]) {
    return (
      <span className="research-evidence-refs">
        {identities.map((identity) => {
          const index = card.evidence.findIndex((item) => item.id === identity);
          return index < 0 ? null : (
            <button key={identity} onClick={() => focusEvidence(identity)}>
              证据 {index + 1}
            </button>
          );
        })}
      </span>
    );
  }
  function entity(identity: string) {
    const mention = [...card.mentions, ...card.events].find(
      (item) => item.id === identity,
    );
    const item = card.items.find((item) => item.id === identity);
    if (!mention) return <span>{item?.title || identity}</span>;
    const name = mention.name;
    return (
      <button
        className="research-inline-link"
        onClick={() => {
          const next = new URLSearchParams(params);
          next.delete("card");
          next.set("entity", mention.entity_id || identity);
          setParams(next);
        }}
      >
        {name}
      </button>
    );
  }
  const adopted = card.state === "human_accepted",
    excluded = card.state === "excluded",
    pending = card.state === "pending";
  const stale = card.source_current === false || card.source_status === "stale";
  const superseded = card.research_current === false;
  const historical = stale || superseded;
  if (fullEditing)
    return (
      <CardEditor cardId={card.id} onClose={() => setFullEditing(false)} />
    );
  return (
    <article className="research-card-detail">
      {params.get("from") === "pending" && (
        <Link className="research-source-link" to="/reviews">
          返回待办
        </Link>
      )}
      <SourceNotice
        bookId={card.book_id}
        stale={stale}
        superseded={superseded}
        revisionPending={card.source_status === "revision_pending"}
      />
      <header>
        <div>
          <span className="research-status" data-state={card.state}>
            {historical
              ? `历史状态：${cardStateLabel(card.state)}`
              : cardStateLabel(card.state)}
          </span>
          <h2>{card.title}</h2>
          {card.book_title && (
            <p className="research-provenance">来源：{card.book_title}</p>
          )}
          <p className="research-provenance">
            {[
              card.document_type,
              card.source_layer,
              dateLabel(card.formation_date) &&
                `成文：${dateLabel(card.formation_date)}`,
              dateLabel(card.event_date) &&
                `事件：${dateLabel(card.event_date)}`,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <a
          className="research-source-link"
          href={research.exportHref(card.id)}
          download
        >
          {historical ? "导出历史卡片" : "导出 Markdown"}
        </a>
      </header>
      {!historical && (
        <QuestionPicker
          material={{ kind: "card", target_id: card.id, title: card.title }}
        />
      )}
      {!historical && (
        <Button
          variant="outline"
          className="research-card-edit-control"
          onClick={() => setFullEditing(true)}
        >
          修订完整卡片
        </Button>
      )}
      {card.metadata && (
        <details className="research-metadata">
          <summary>编目与形成信息</summary>
          <p>
            标题来源：
            {
              {
                source: "原文题名",
                supplied: "已有编目",
                system: "系统拟题",
                unknown: "未明",
              }[card.metadata.title_origin]
            }
          </p>
          <dl>
            {Object.entries({
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
            }).map(([key, label]) => {
              const value =
                card.metadata?.[
                  key as Exclude<
                    keyof NonNullable<typeof card.metadata>,
                    "title_origin"
                  >
                ];
              return (
                <div key={key}>
                  <dt>{label}</dt>
                  <dd>
                    {value?.certainty === "unknown"
                      ? "未详"
                      : value?.value || "未记录"}
                    {value?.certainty &&
                      value.certainty !== "unknown" &&
                      ` · ${certaintyLabels[value.certainty]}`}
                    {value?.note && <p>{value.note}</p>}
                    {refs(value?.evidence_ids ?? [])}
                  </dd>
                </div>
              );
            })}
          </dl>
        </details>
      )}
      <div className="research-card-columns">
        <div className="research-card-analysis">
          {pending && !!card.checks?.issues.length && (
            <section className="research-verification-issues">
              <h3>需要核验的问题</h3>
              <ul>
                {card.checks.issues.map((issue, index) => (
                  <li key={index}>{issue}</li>
                ))}
              </ul>
            </section>
          )}
          <section>
            <h3>
              {card.human_decision?.action === "edit"
                ? "人工修订提要"
                : "模型提要"}
            </h3>
            <p className="research-preserve">{card.summary}</p>
          </section>
          {card.items.map((item) => (
            <section key={item.id}>
              <p className="research-provenance">
                {kindLabels[item.kind] ?? item.kind}
                {item.epistemic_state
                  ? ` · ${certaintyLabels[item.epistemic_state] ?? item.epistemic_state}`
                  : ""}
              </p>
              <h3>{item.title}</h3>
              <p className="research-preserve">{item.text}</p>
              {item.attribution && <p>归属：{item.attribution}</p>}
              {refs(item.evidence_ids)}
              {!!item.limitations?.length && (
                <p>使用边界：{item.limitations.join("；")}</p>
              )}
            </section>
          ))}
          <section className="research-boundaries">
            <h3>使用边界</h3>
            {card.limitations.length ? (
              <ul>
                {card.limitations.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            ) : (
              <p>尚未记录额外使用边界，引用前仍需核对原文与上下文。</p>
            )}
            {card.no_argument_reason && <p>{card.no_argument_reason}</p>}
            {!!card.alternatives.length && (
              <>
                <h4>其他解释</h4>
                <ul>
                  {card.alternatives.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              </>
            )}
            {!!card.questions.length && (
              <>
                <h4>待解决问题</h4>
                <ul>
                  {card.questions.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              </>
            )}
          </section>
          {!!card.mentions.length && (
            <section>
              <h3>人物与实体</h3>
              {card.mentions.map((item) => (
                <p key={item.id}>
                  {entity(item.id)}
                  <small> · {kindLabels[item.kind] ?? item.kind}</small>
                  {item.identity_basis && <span> · {item.identity_basis}</span>}
                  {refs(item.evidence_ids)}
                </p>
              ))}
            </section>
          )}
          {!!card.events.length && (
            <section>
              <h3>事件</h3>
              {card.events.map((item) => (
                <div key={item.id}>
                  <p>
                    {entity(item.id)}
                    {dateLabel(item.date) && ` · ${dateLabel(item.date)}`}
                  </p>
                  {item.description && <p>{item.description}</p>}
                  <EventNature value={item} />
                  {item.action && <p>行为：{item.action}</p>}
                  {item.place?.value && (
                    <p>
                      地点：{item.place.value} ·{" "}
                      {certaintyLabels[item.place.certainty]}
                    </p>
                  )}
                  {!!item.participant_roles?.length ? (
                    <ul>
                      {item.participant_roles.map((role, index) => (
                        <li key={index}>
                          {entity(role.mention_id)} · {relationLabel(role.role)}
                          <p>{role.description}</p>
                          {refs(role.evidence_ids)}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    !!item.participant_ids?.length && (
                      <p>
                        参与者：
                        {item.participant_ids.map((identity) => (
                          <span key={identity}>{entity(identity)} </span>
                        ))}
                      </p>
                    )
                  )}
                  {refs(item.evidence_ids)}
                </div>
              ))}
            </section>
          )}
          {!!card.relations.length && (
            <section>
              <h3>关系与依据</h3>
              {card.relations.map((item) => (
                <div className="research-relation" key={item.id}>
                  <p>
                    {entity(item.subject_id)}{" "}
                    <strong>{relationLabel(item.predicate)}</strong>{" "}
                    {entity(item.object_id)}
                    {item.role && ` · ${relationLabel(item.role)}`}
                  </p>
                  {item.reason && <p>{item.reason}</p>}
                  {refs(item.evidence_ids)}
                </div>
              ))}
            </section>
          )}
          <CrossCardRelations
            relations={card.cross_relations ?? []}
            evidence={card.evidence}
            bookId={card.book_id}
          />
          <DerivedCardRelations relations={card.derived_relations} />
          {!!card.coverage?.length && (
            <details className="research-coverage">
              <summary>全文提取覆盖 · {card.coverage.length} 段</summary>
              {card.coverage.map((unit) => (
                <section key={unit.id}>
                  <p>{unit.summary}</p>
                  <p className="research-provenance">
                    {unit.mention_ids?.length ?? 0} 项实体 ·{" "}
                    {unit.event_ids?.length ?? 0} 项事件 ·{" "}
                    {unit.relation_ids?.length ?? 0} 项关系
                  </p>
                  {unit.no_mentions_reason && (
                    <p>未提取实体：{unit.no_mentions_reason}</p>
                  )}
                  {unit.no_events_reason && (
                    <p>未提取事件：{unit.no_events_reason}</p>
                  )}
                  {unit.no_relations_reason && (
                    <p>未提取关系：{unit.no_relations_reason}</p>
                  )}
                </section>
              ))}
            </details>
          )}
          {!!card.research_plan?.length && (
            <details className="research-investigation">
              <summary>补证问题与未解事项</summary>
              {card.research_plan.map((item) => (
                <section key={item.id}>
                  <h4>{item.question}</h4>
                  <p>{item.evaluation}</p>
                  {!!item.missing_evidence?.length && (
                    <ul>
                      {item.missing_evidence.map((missing, index) => (
                        <li key={index}>{missing}</li>
                      ))}
                    </ul>
                  )}
                  {item.stop_reason && <p>本次查证范围：{item.stop_reason}</p>}
                  {refs(item.evidence_ids ?? [])}
                </section>
              ))}
            </details>
          )}
        </div>
        <aside className="research-source-pane">
          <EvidenceOriginal evidence={card.evidence} selected={selected} />
          <Evidence
            evidence={card.evidence}
            scope={evidenceScope}
            bookId={card.book_id}
            selected={selected}
            historical={historical}
          />
        </aside>
      </div>
      <section className="research-human-decision" aria-label="人工决策">
        <h3>人工决策</h3>
        {historical ? (
          <p>
            历史记录保留原有判断，当前版本的核验与人工决定请在新研究记录中处理。
          </p>
        ) : adopted ? (
          <p>你的决定已保存，原有证据与版本继续保留。</p>
        ) : excluded ? (
          <p>此卡已排除，原有证据和版本继续保留。</p>
        ) : !pending ? (
          <p>已完成原文核验，引用前可继续核对原文与使用边界。</p>
        ) : (
          <>
            <p>
              请核对右侧原文，再决定是否采用此卡。模型核验结果不代表你的确认。
            </p>
            {editing ? (
              <>
                <label htmlFor="research-summary">修订提要</label>
                <textarea
                  id="research-summary"
                  value={text}
                  onChange={(event) => setText(event.target.value)}
                  rows={5}
                  maxLength={60000}
                />
                <Button
                  disabled={decision.isPending || !text.trim()}
                  onClick={() =>
                    decision.mutate({ action: "edit", text: text.trim() })
                  }
                >
                  保存修订并确认
                </Button>
                <Button
                  variant="ghost"
                  disabled={decision.isPending}
                  onClick={() => setEditing(false)}
                >
                  取消修订
                </Button>
              </>
            ) : (
              <div className="research-decision-actions">
                <Button
                  disabled={decision.isPending}
                  onClick={() => decision.mutate({ action: "accept" })}
                >
                  确认采用此卡
                </Button>
                <Button
                  variant="outline"
                  disabled={decision.isPending}
                  onClick={() => {
                    setText(card.summary);
                    setEditing(true);
                  }}
                >
                  修订提要
                </Button>
                <Button
                  variant="ghost"
                  disabled={decision.isPending}
                  onClick={() => decision.mutate({ action: "reject" })}
                >
                  排除此卡
                </Button>
              </div>
            )}
          </>
        )}
        {decision.isPending && <p role="status">正在保存人工决定…</p>}
        {pendingNavigation.notice && (
          <p role="status">{pendingNavigation.notice}</p>
        )}
        {decision.isError && (
          <p role="alert">
            {decision.error.message}
            <Button
              variant="outline"
              onClick={() => {
                void query.invalidateQueries({
                  queryKey: researchKeys.card(card.id),
                });
              }}
            >
              重新读取当前卡片
            </Button>
            {editing && " 当前修订草稿仍保留，请对照新版本后再提交。"}
          </p>
        )}
      </section>
    </article>
  );
}

function ResearchCardDetail({ cardId }: { cardId: string }) {
  const detail = useQuery({
    queryKey: researchKeys.card(cardId),
    queryFn: ({ signal }) => research.card(cardId, signal),
    staleTime: 30000,
    refetchOnWindowFocus: false,
  });
  if (detail.isPending) return <p role="status">正在读取史料卡…</p>;
  if (detail.isError)
    return (
      <p role="alert">
        {detail.error.message}
        {detail.error instanceof ResearchError &&
        detail.error.status === 404 ? (
          <Link className="research-source-link" to="/">
            返回书籍
          </Link>
        ) : (
          <Button onClick={() => void detail.refetch()}>重新读取史料卡</Button>
        )}
      </p>
    );
  return <CardContent key={cardId} card={detail.data} />;
}

function ResearchEntityDetail({ entityId }: { entityId: string }) {
  const [params, setParams] = useSearchParams();
  const [editingIdentity, setEditingIdentity] = useState(false),
    [graphOpen, setGraphOpen] = useState(false),
    [timelineOpen, setTimelineOpen] = useState(false);
  const detail = useQuery({
    queryKey: researchKeys.entity(entityId),
    queryFn: ({ signal }) => research.entity(entityId, signal),
    staleTime: 30000,
    refetchOnWindowFocus: false,
  });
  if (detail.isPending) return <p role="status">正在读取人物与事件…</p>;
  if (detail.isError)
    return (
      <p role="alert">
        {detail.error.message}
        {detail.error instanceof ResearchError &&
        detail.error.status === 404 ? (
          <Link className="research-source-link" to="/">
            返回书籍
          </Link>
        ) : (
          <Button onClick={() => void detail.refetch()}>
            重新读取人物与事件
          </Button>
        )}
      </p>
    );
  const entity = detail.data;
  const historical =
    entity.source_current === false ||
    entity.source_status === "stale" ||
    entity.research_current === false;
  const relatedCards = [
    ...new Map(
      [
        ...(entity.mentions ?? []).map((mention) => ({
          ...mention,
          name: mention.card_title || mention.name,
        })),
        {
          card_id: entity.card_id,
          name: entity.card_title || entity.name,
          state: entity.state,
        },
      ].map((item) => [item.card_id, item]),
    ).values(),
  ];
  function openCard(identity: string) {
    const next = new URLSearchParams(params);
    next.delete("entity");
    next.set("card", identity);
    setParams(next);
  }
  function openEntity(identity: string) {
    const next = new URLSearchParams(params);
    next.delete("card");
    next.set("entity", identity);
    setParams(next);
  }
  if (editingIdentity)
    return (
      <IdentityEditor
        entityId={entityId}
        onClose={() => setEditingIdentity(false)}
        onSaved={(saved) => {
          setEditingIdentity(false);
          openEntity(saved.canonical_id || saved.id);
        }}
      />
    );
  return (
    <article className="research-entity-detail">
      <SourceNotice
        bookId={entity.book_id}
        stale={
          entity.source_current === false || entity.source_status === "stale"
        }
        superseded={entity.research_current === false}
        revisionPending={entity.source_status === "revision_pending"}
      />
      <p className="research-provenance">
        {kindLabels[entity.kind] ?? entity.kind}
      </p>
      <h2>{entity.name}</h2>
      {entity.kind === "event" && <EventNature value={entity} />}
      {(entity.historical_aliases.length > 0 ||
        entity.withdrawn_aliases.length > 0) && (
        <details>
          <summary>历史及已撤回别名</summary>
          {entity.historical_aliases.map((alias) => (
            <p key={`historical-${alias.name}`}>
              历史来源别名：{alias.name} · 来源已有后续版本
            </p>
          ))}
          {entity.withdrawn_aliases.map((alias) => (
            <p key={`withdrawn-${alias.name}`}>
              已人工撤回：{alias.name} · 保留历史决定
            </p>
          ))}
        </details>
      )}
      {!!entity.aliases.length && (
        <p>
          来源别名：
          {[...new Set(entity.aliases.map((alias) => alias.name))].join("、")}
        </p>
      )}
      {entity.canonical_id && !historical && (
        <div className="research-decision-actions">
          <QuestionPicker
            material={{
              kind: "entity",
              target_id: entity.canonical_id,
              title: entity.name,
            }}
          />
          <Button variant="outline" onClick={() => setEditingIdentity(true)}>
            核对身份与别名
          </Button>
          <Button
            variant="outline"
            aria-expanded={graphOpen}
            onClick={() => setGraphOpen(!graphOpen)}
          >
            {graphOpen ? "收起关系视图" : "查看关系视图"}
          </Button>
          <Button
            variant="outline"
            aria-expanded={timelineOpen}
            onClick={() => setTimelineOpen(!timelineOpen)}
          >
            {timelineOpen ? "收起事项时间线" : "查看事项时间线"}
          </Button>
        </div>
      )}
      {graphOpen && (
        <Suspense fallback={<p role="status">正在打开关系视图…</p>}>
          <ResearchGraph
            entityId={entity.canonical_id || entity.id}
            onEntity={openEntity}
            onCard={openCard}
          />
        </Suspense>
      )}
      {entity.identity_basis && <p>身份依据：{entity.identity_basis}</p>}
      {timelineOpen && (
        <EntityTimeline
          entityId={entity.canonical_id || entity.id}
          onEntity={openEntity}
          onCard={openCard}
        />
      )}
      {entity.description && <p>{entity.description}</p>}
      {dateLabel(entity.date) && <p>{dateLabel(entity.date)}</p>}
      {!!entity.source_mentions.length && (
        <section>
          <h3>各处来源记载</h3>
          {entity.source_mentions.map((mention) => (
            <article className="research-result" key={mention.id}>
              <h4>{mention.card_title || mention.name}</h4>
              <p>{mention.identity_basis}</p>
              <Button variant="ghost" onClick={() => openCard(mention.card_id)}>
                查看这处记载的史料卡
              </Button>
              <Evidence
                evidence={(mention.evidence ?? []).map((span) => ({
                  ...span,
                  pages: span.pages ?? [],
                  footnote_ids: span.footnote_ids ?? [],
                }))}
                bookId={mention.book_id}
                historical={mention.source_current === false}
              />
            </article>
          ))}
        </section>
      )}
      {!!entity.events.length && (
        <section>
          <h3>关联事件</h3>
          {entity.events.map((event) => (
            <article className="research-result" key={event.id}>
              <button
                className="research-inline-link"
                onClick={() => openEntity(event.id)}
              >
                {event.name}
              </button>
              {dateLabel(event.date) && <p>{dateLabel(event.date)}</p>}
              {event.action && <p>行为：{event.action}</p>}
              <EventNature value={event} />
              {event.place?.value && <p>地点：{event.place.value}</p>}
              <ul>
                {event.participants?.map((participant) => (
                  <li key={`${event.id}-${participant.entity_id}`}>
                    <button
                      className="research-inline-link"
                      onClick={() => openEntity(participant.entity_id)}
                    >
                      {participant.name || "查看参与者"}
                    </button>{" "}
                    · {relationLabel(participant.role)}
                  </li>
                ))}
              </ul>
              <Button variant="ghost" onClick={() => openCard(event.card_id)}>
                查看事件出处卡
              </Button>
            </article>
          ))}
        </section>
      )}
      {!!entity.participations.length && (
        <section>
          <h3>{entity.kind === "event" ? "参与者与角色" : "人物活动与角色"}</h3>
          {entity.participations.map((item) => (
            <article className="research-result" key={item.id}>
              <button
                className="research-inline-link"
                onClick={() =>
                  openEntity(
                    entity.kind === "event" ? item.target_id : item.event_id,
                  )
                }
              >
                {entity.kind === "event"
                  ? item.participant_name || "查看参与者"
                  : item.event_name || "查看事件"}
              </button>{" "}
              · {relationLabel(item.role || item.predicate, item.kind)}
              <p>{item.reason}</p>
              {dateLabel(item.date) && <p>{dateLabel(item.date)}</p>}
              <EventNature value={item} />
              <Button variant="ghost" onClick={() => openCard(item.card_id)}>
                核对角色原文依据
              </Button>
            </article>
          ))}
        </section>
      )}
      {!!entity.relationships.length && (
        <section>
          <h3>关系陈述与来源</h3>
          {entity.relationships.map((item) => (
            <article className="research-relation" key={item.id}>
              <strong>{relationLabel(item.predicate, item.kind)}</strong>
              <p>{item.reason}</p>
              <QuestionPicker
                material={{
                  kind: "relation",
                  target_id: item.id,
                  title: relationLabel(item.predicate, item.kind),
                }}
              />
              <div className="research-decision-actions">
                <Button
                  variant="ghost"
                  onClick={() =>
                    openEntity(
                      item.source_id === entity.id
                        ? item.target_id
                        : item.source_id,
                    )
                  }
                >
                  查看关联另一端
                </Button>
                <Button variant="ghost" onClick={() => openCard(item.card_id)}>
                  核对本端证据
                </Button>
                {item.target_card_id && (
                  <Button
                    variant="ghost"
                    onClick={() => openCard(item.target_card_id!)}
                  >
                    核对对方证据
                  </Button>
                )}
              </div>
            </article>
          ))}
        </section>
      )}
      {!!entity.identity_decisions.length && (
        <details>
          <summary>身份判断与修订历史</summary>
          {entity.identity_decisions.map((item) => (
            <section key={item.id}>
              <p>
                {item.approval_type === "user" ? "人工决定" : "模型核验"} ·{" "}
                {{
                  merge: "合并同一身份",
                  distinct: "确认为不同身份",
                  split: "拆分来源记载",
                  alias: "记录别名",
                  alias_remove: "移除别名",
                }[item.action] ?? item.action}
              </p>
              <p>{item.reason}</p>
              {item.evidence_refs?.map((ref, index) => (
                <Button
                  key={index}
                  variant="ghost"
                  onClick={() => openCard(ref.card_id)}
                >
                  查看判断依据 {index + 1}
                </Button>
              ))}
            </section>
          ))}
        </details>
      )}
      <h3>相关史料卡</h3>
      {relatedCards.map((card) => (
        <article className="research-result" key={card.card_id}>
          <button
            className="research-result-title"
            onClick={() => openCard(card.card_id)}
          >
            {card.name}
          </button>
          {card.state && (
            <span className="research-status" data-state={card.state}>
              {cardStateLabel(card.state)}
            </span>
          )}
        </article>
      ))}
      {!!entity.identity_links?.length && (
        <section>
          <h3>身份关联与依据</h3>
          {entity.identity_links.map((link, index) => (
            <div className="research-relation" key={index}>
              <p>
                {link.relation === "same_as"
                  ? "有证据支持同一身份"
                  : "可能为同一身份 · 尚需核实"}
              </p>
              <p>{link.reason}</p>
              <Button
                variant="ghost"
                onClick={() =>
                  openEntity(
                    link.entity_id === entity.id
                      ? link.source_entity_id
                      : link.entity_id,
                  )
                }
              >
                查看关联条目
              </Button>
              <Button variant="ghost" onClick={() => openCard(link.card_id)}>
                查看关联证据卡
              </Button>
            </div>
          ))}
        </section>
      )}
      <Evidence
        evidence={entity.evidence ?? []}
        bookId={entity.book_id}
        historical={historical}
      />
    </article>
  );
}

export default function ResearchDetail() {
  const [params, setParams] = useSearchParams();
  const cardId = params.get("card"),
    entityId = params.get("entity");
  if (!cardId && !entityId) return null;
  const returnTo = params.get("returnTo");
  const readerReturn =
    returnTo &&
    /^\/books\/[A-Za-z0-9_-]+\?[^#]*$/.test(returnTo) &&
    new URLSearchParams(returnTo.slice(returnTo.indexOf("?") + 1)).get(
      "tab",
    ) === "reader"
      ? returnTo
      : null;
  return (
    <aside className="research-detail-pane">
      <div className="research-detail-toolbar">
        <span>{cardId ? "史料卡详情" : "人物与事件详情"}</span>
        {readerReturn && <Link to={readerReturn}>返回阅读位置</Link>}
        <Button
          size="icon"
          variant="ghost"
          aria-label="关闭详情"
          onClick={() => {
            const next = new URLSearchParams(params);
            next.delete("card");
            next.delete("entity");
            setParams(next);
          }}
        >
          <X size={18} />
        </Button>
      </div>
      {cardId ? (
        <ResearchCardDetail cardId={cardId} />
      ) : (
        <ResearchEntityDetail entityId={entityId!} />
      )}
    </aside>
  );
}
