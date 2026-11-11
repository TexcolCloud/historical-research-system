import { useQuery } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  normalizeEvidence,
  research,
  researchKeys,
  type ResearchEvidence,
  type ResearchTimelineItem,
} from "./api";
import type { NetworkSelection } from "./network-query";
import Evidence from "./ResearchEvidence";
import EvidenceOriginal from "./EvidenceOriginal";
import EventNature, { EventParticipants } from "./EventNature";
import { relationLabel } from "./relation-labels";
import { entityLabels } from "./labels";

export default function NetworkEvidence({
  selection,
  item,
  itemPending,
  itemError,
  bookId,
  onCenter,
  onTimeline,
  onClear,
  onReload,
}: {
  selection: NetworkSelection;
  item?: ResearchTimelineItem;
  itemPending?: boolean;
  itemError?: string;
  bookId: string;
  onCenter: (id: string) => void;
  onTimeline: (id: string) => void;
  onClear: () => void;
  onReload?: () => void;
}) {
  const location = useLocation();
  const entity = useQuery({
    queryKey: researchKeys.entity(selection.id),
    queryFn: ({ signal }) => research.entity(selection.id, signal),
    enabled: selection.kind === "entity" && !!selection.id,
    staleTime: 30_000,
    retry: false,
  });
  const relation = useQuery({
    queryKey: ["research", "network-relation", selection.id],
    queryFn: ({ signal }) => research.networkRelation(selection.id, signal),
    enabled: selection.kind === "relation" && !!selection.id,
    staleTime: 30_000,
    retry: false,
  });
  const cardId =
    item?.card_id || (selection.kind === "document" ? selection.id : "");
  const card = useQuery({
    queryKey: researchKeys.card(cardId),
    queryFn: ({ signal }) => research.card(cardId, signal),
    enabled: !!cardId && ["event", "document"].includes(selection.kind),
    staleTime: 30_000,
    retry: false,
  });
  const active =
    selection.kind === "entity"
      ? entity
      : selection.kind === "relation"
        ? relation
        : card;
  const current = active.data;
  const returnTo = `${location.pathname}${location.search}`;
  const cardHref = (id: string) =>
    `/research?${new URLSearchParams({ card: id, returnTo })}`;
  if (!selection.id)
    return (
      <div className="network-evidence-empty">
        <h2>原文依据</h2>
        <p>选择一个实体、关系或时间事项，查看来源记载。</p>
      </div>
    );
  if (selection.kind === "event" && !item)
    return itemError ? (
      <div role="alert">
        <h2>暂时无法定位事项</h2>
        <p>{itemError}</p>
        <Button onClick={onReload}>重新读取事项</Button>
      </div>
    ) : itemPending ? (
      <p role="status">正在定位所选事项…</p>
    ) : (
      <div role="status">
        <h2>事项不在当前清单</h2>
        <p>来源或筛选范围可能已经更新，请从当前清单重新选择。</p>
        <Button onClick={onClear}>返回当前关注实体</Button>
      </div>
    );
  if (active.isError)
    return (
      <div role="alert">
        <h2>暂时无法读取依据</h2>
        <p>{active.error.message}</p>
        <Button onClick={() => void active.refetch()}>重新读取依据</Button>
      </div>
    );
  if (!current) return <p role="status">正在读取所选记录的依据…</p>;
  if (item && card.data && item.card_version !== card.data.version)
    return (
      <article>
        <h2>{item.name}</h2>
        <p role="alert">
          来源版本已变化，请重新读取事项与依据。旧事项不会与新版原文拼接展示。
        </p>
        <Button
          onClick={() => {
            void card.refetch();
            onReload?.();
          }}
        >
          重新读取事项与依据
        </Button>
        <Link className="research-source-link" to={cardHref(card.data.id)}>
          查看当前史料卡
        </Link>
      </article>
    );
  let evidence: ResearchEvidence[] = [];
  let sourceBook = bookId;
  let title = "";
  let description = "";
  let sourceCard = "";
  let stale = false;
  if (selection.kind === "entity" && entity.data) {
    const value = entity.data;
    title = value.name;
    description = value.identity_basis || value.description;
    sourceBook = value.book_id;
    sourceCard = value.card_id;
    const mentions = value.source_mentions.filter(
      (mention) => !bookId || mention.book_id === bookId,
    );
    if (bookId) {
      sourceBook = bookId;
      sourceCard = mentions[0]?.card_id || "";
      description =
        mentions[0]?.identity_basis || "当前范围内的来源记载见下方原文。";
    }
    evidence = mentions.length
      ? mentions.flatMap((mention) =>
          (mention.evidence ?? []).map((span) =>
            normalizeEvidence({
              ...span,
              book_id: span.book_id || mention.book_id,
            }),
          ),
        )
      : value.evidence.filter(
          (span) => !bookId || (span.book_id || value.book_id) === bookId,
        );
    stale = value.source_current === false || value.research_current === false;
  } else if (selection.kind === "relation" && relation.data) {
    const value = relation.data;
    title = relationLabel(value.predicate, value.kind);
    description = value.reason;
    sourceBook = value.book_id;
    sourceCard = value.card_id;
    evidence = (value.evidence ?? []).map(normalizeEvidence);
    stale = value.source_current === false || value.target_current === false;
  } else if (card.data) {
    const value = card.data;
    title = item?.name || value.title;
    description = item?.description || value.summary;
    sourceBook = value.book_id;
    sourceCard = value.id;
    evidence = item
      ? value.evidence.filter((span) =>
          (item.evidence_ids ?? []).includes(span.id),
        )
      : value.evidence;
    stale =
      value.source_current === false ||
      value.research_current === false ||
      Boolean(item && item.card_version !== value.version);
  }
  const unique = [
    ...new Map(
      evidence.map((span) => [
        `${span.book_id || sourceBook}:${span.article_id}:${span.id}`,
        span,
      ]),
    ).values(),
  ];
  const date =
    item?.date || (selection.kind === "relation" ? relation.data?.date : null);
  const dateBasis = item?.date_basis || relation.data?.date_basis;
  const revisionPending =
    item?.needs_update ||
    entity.data?.source_status === "revision_pending" ||
    card.data?.source_status === "revision_pending" ||
    relation.data?.needs_update;
  return (
    <article className="network-evidence-content">
      <p className="network-eyebrow">
        {selection.kind === "relation"
          ? "关系记载"
          : selection.kind === "entity"
            ? entityLabels[entity.data?.kind || ""] || "实体"
            : item?.date_basis === "formation"
              ? "文献形成日期"
              : "事项记载"}
      </p>
      <h2>{title}</h2>
      {description && <p className="research-preserve">{description}</p>}
      {date?.text && (
        <p className="research-provenance">
          {dateBasis === "formation" ? "文献形成日期" : "事项原文日期"}：
          {date.text} ·{" "}
          {date.certainty === "inferred"
            ? "据材料推定"
            : date.certainty === "disputed"
              ? "日期有争议"
              : date.certainty === "unknown"
                ? "日期未明"
                : "原文记载"}
        </p>
      )}
      {date?.text && dateBasis === "formation" && (
        <p className="research-provenance">该日期不代表事项发生日期。</p>
      )}
      {item?.item_kind === "event" && <EventNature value={item} />}
      {item?.item_kind === "event" && <EventParticipants value={item} />}
      {selection.kind === "relation" &&
        relation.data?.event_status &&
        relation.data.event_status !== "unknown" && (
          <EventNature value={relation.data} />
        )}
      {selection.kind === "entity" && entity.data?.kind === "event" && (
        <EventNature value={entity.data} />
      )}
      {stale ? (
        <p role="alert">
          来源已修订或记录已失效。请重新读取当前事项后核对，当前依据仅供历史查阅。
        </p>
      ) : (
        <p className="research-provenance">
          以下是材料中的记载与解释，保留其范围和条件。
        </p>
      )}
      {revisionPending && !stale && (
        <p role="note" className="research-provenance">
          原文修订核验中，当前仍展示此前已发布版本的记录。
        </p>
      )}
      <div className="network-evidence-actions">
        {selection.kind === "entity" && (
          <>
            <Button onClick={() => onCenter(selection.id)}>以此为中心</Button>
            <Button variant="outline" onClick={() => onTimeline(selection.id)}>
              查看相关时间线
            </Button>
          </>
        )}
        {item?.entity_id && (
          <Button onClick={() => onCenter(item.entity_id!)}>
            在关系网络中查看
          </Button>
        )}
        {sourceCard && (
          <Link className="research-source-link" to={cardHref(sourceCard)}>
            查看出处史料卡
          </Link>
        )}
        {selection.kind === "entity" && (
          <Link
            className="research-source-link"
            to={`/research?${new URLSearchParams({ entity: selection.id, returnTo })}`}
          >
            查看完整实体档案
          </Link>
        )}
      </div>
      {relation.data?.target_card_id && selection.kind === "relation" && (
        <Link
          className="research-source-link"
          to={cardHref(relation.data.target_card_id)}
        >
          查看另一端出处史料卡
        </Link>
      )}
      {selection.kind === "relation" && relation.data && (
        <div className="network-relation-endpoints">
          <Button
            variant="outline"
            onClick={() => onCenter(relation.data!.source_id)}
          >
            以关系起点为中心
          </Button>
          <Button
            variant="outline"
            onClick={() => onCenter(relation.data!.target_id)}
          >
            以关系另一端为中心
          </Button>
        </div>
      )}
      <Evidence
        evidence={unique.slice(0, 4)}
        bookId={sourceBook}
        historical={stale}
      />
      {unique.length > 4 && (
        <details>
          <summary>其余 {unique.length - 4} 条原文依据</summary>
          <Evidence
            evidence={unique.slice(4)}
            bookId={sourceBook}
            historical={stale}
            offset={4}
          />
        </details>
      )}
      <EvidenceOriginal evidence={unique} />
    </article>
  );
}
