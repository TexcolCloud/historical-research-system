import { useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import Evidence from "./ResearchEvidence";
import IdentityEditor from "./IdentityEditor";
import EventNature from "./EventNature";
import { relationLabel } from "./relation-labels";
import { entityLabels } from "./labels";
import SourceCardReader from "./SourceCardReader";
import { cardStateLabel } from "./card-reading";
import { entityWorkspaceHref } from "./network-query";
import {
  research,
  researchKeys,
  ResearchError,
  type ResearchCard,
} from "./api";

export { cardStateLabel } from "./card-reading";
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
  ...entityLabels,
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
  return (
    <SourceCardReader
      key={cardId}
      card={detail.data}
      notice={
        <SourceNotice
          bookId={detail.data.book_id}
          stale={
            detail.data.source_current === false ||
            detail.data.source_status === "stale"
          }
          superseded={detail.data.research_current === false}
          revisionPending={detail.data.source_status === "revision_pending"}
        />
      }
    />
  );
}

function ResearchEntityDetail({ entityId }: { entityId: string }) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const [editingIdentity, setEditingIdentity] = useState(false);
  const detail = useQuery({
    queryKey: researchKeys.entity(entityId),
    queryFn: ({ signal }) => research.entity(entityId, signal),
    staleTime: 30000,
    refetchOnWindowFocus: false,
  });
  if (detail.isPending) return <p role="status">正在读取实体…</p>;
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
          <Button onClick={() => void detail.refetch()}>重新读取实体</Button>
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
          <Link
            className="research-source-link"
            to={entityWorkspaceHref(
              entity.canonical_id || entity.id,
              location.pathname.startsWith("/books/")
                ? location.pathname.split("/")[2]
                : params.get("book_id") || "",
            )}
          >
            打开关系与时间线
          </Link>
          <Button variant="outline" onClick={() => setEditingIdentity(true)}>
            核对身份与别名
          </Button>
        </div>
      )}
      {entity.identity_basis && <p>身份依据：{entity.identity_basis}</p>}
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
          <h3>{entity.kind === "event" ? "参与者与角色" : "相关活动与角色"}</h3>
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
  const workspaceReturn =
    returnTo?.startsWith("/research?") &&
    !returnTo.includes("#") &&
    new URLSearchParams(returnTo.slice(returnTo.indexOf("?") + 1)).get(
      "view",
    ) === "network"
      ? returnTo
      : null;
  return (
    <aside className="research-detail-pane" data-document={Boolean(cardId)}>
      <div
        className={`research-detail-toolbar${cardId && !readerReturn ? " dossier-close-toolbar" : ""}`}
      >
        {!cardId && <span>实体详情</span>}
        {readerReturn && <Link to={readerReturn}>返回阅读位置</Link>}
        {workspaceReturn && <Link to={workspaceReturn}>返回关系与时间线</Link>}
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
