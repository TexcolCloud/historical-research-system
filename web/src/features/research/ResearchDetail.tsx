import { useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { readingHref } from "@/features/library/reader-location";
import {
  research,
  researchKeys,
  ResearchError,
  type ResearchCard,
  type ResearchEvidence,
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
const roleLabels: Record<string, string> = {
  support: "支持",
  limit: "限制",
  counter: "反证",
  background: "背景",
  association: "关联",
};

function Evidence({
  evidence,
  bookId,
  selected,
  historical = false,
}: {
  evidence: ResearchEvidence[];
  bookId: string;
  selected?: string;
  historical?: boolean;
}) {
  const location = useLocation();
  const returnTo = `${location.pathname}${location.search}`;
  return (
    <section className="research-evidence" aria-label="原文证据">
      <h3>原文与出处</h3>
      {evidence.length ? (
        evidence.map((item, index) => (
          <article
            key={item.id}
            id={`research-evidence-${item.id}`}
            data-selected={selected === item.id}
          >
            <div className="research-evidence-heading">
              <strong>证据 {index + 1}</strong>
              <span>PDF 第 {item.pages.join("、")} 页</span>
            </div>
            <blockquote>{item.quote}</blockquote>
            {!!item.footnotes?.length ? (
              <details className="research-footnotes">
                <summary>关联注释 · {item.footnotes.length} 项</summary>
                {item.footnotes.map((note) => (
                  <div key={note.id}>
                    {note.text && (
                      <p className="research-preserve">{note.text}</p>
                    )}
                    {note.reading_target && (
                      <Link
                        className="research-source-link"
                        to={readingHref(
                          item.book_id || bookId,
                          note.reading_target,
                          { returnTo },
                        )}
                      >
                        阅读注释 {note.label}
                      </Link>
                    )}
                  </div>
                ))}
              </details>
            ) : (
              !!item.footnote_ids.length && (
                <p className="research-provenance">
                  关联原文注释 {item.footnote_ids.length} 项，请打开原文核对。
                </p>
              )
            )}
            {item.reading_target ? (
              <Link
                className="research-source-link"
                to={readingHref(item.book_id || bookId, item.reading_target, {
                  returnTo,
                })}
              >
                {historical ? "阅读历史原文" : "阅读原文"} · PDF 第{" "}
                {item.pages.join("、")} 页
                <ArrowUpRight size={15} aria-hidden="true" />
              </Link>
            ) : (
              <p className="research-provenance">
                原文位置尚未可靠关联，不能自动定位。
              </p>
            )}
          </article>
        ))
      ) : (
        <p>尚无已保存的原文证据。</p>
      )}
    </section>
  );
}

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
  const query = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<string>();
  const [editing, setEditing] = useState(false);
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
    },
  });
  function focusEvidence(identity: string) {
    setSelected(identity);
    document
      .getElementById(`research-evidence-${identity}`)
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
  return (
    <article className="research-card-detail">
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
                    {entity(item.subject_id)} <strong>{item.predicate}</strong>{" "}
                    {entity(item.object_id)}
                    {item.role && ` · ${roleLabels[item.role] ?? item.role}`}
                  </p>
                  {item.reason && <p>{item.reason}</p>}
                  {refs(item.evidence_ids)}
                </div>
              ))}
            </section>
          )}
        </div>
        <Evidence
          evidence={card.evidence}
          bookId={card.book_id}
          selected={selected}
          historical={historical}
        />
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
        {decision.isError && (
          <p role="alert">
            {decision.error.message}
            <Button
              variant="outline"
              onClick={() => {
                setEditing(false);
                decision.reset();
                void query.invalidateQueries({
                  queryKey: researchKeys.card(card.id),
                });
              }}
            >
              重新读取当前卡片
            </Button>
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
      {entity.identity_basis && <p>身份依据：{entity.identity_basis}</p>}
      {entity.description && <p>{entity.description}</p>}
      {dateLabel(entity.date) && <p>{dateLabel(entity.date)}</p>}
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
  return (
    <aside className="research-detail-pane">
      <div className="research-detail-toolbar">
        <span>{cardId ? "史料卡详情" : "人物与事件详情"}</span>
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
