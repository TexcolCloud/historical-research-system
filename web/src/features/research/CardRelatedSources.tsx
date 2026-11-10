import type { ReactNode } from "react";
import CrossCardRelations from "./CrossCardRelations";
import DerivedCardRelations from "./DerivedCardRelations";
import EventNature from "./EventNature";
import { entityLabels } from "./labels";
import { relationLabel } from "./relation-labels";
import { dateLabel, type ReadingCard } from "./card-reading";
import { ReadingDetails } from "./CardDossierSections";

export default function CardRelatedSources({
  card,
  entity,
  refs,
}: {
  card: ReadingCard;
  entity: (id: string) => ReactNode;
  refs: (ids: string[]) => ReactNode;
}) {
  return (
    <>
      <h3>关联实体</h3>
      {card.mentions.length ? (
        <div className="dossier-entity-index">
          {card.mentions.map((item) => (
            <span key={item.id}>
              {entity(item.id)}
              <small> · {entityLabels[item.kind] || item.kind}</small>
            </span>
          ))}
        </div>
      ) : (
        <p className="dossier-missing">本卡未记录关联实体。</p>
      )}
      {!!card.mentions.length && (
        <ReadingDetails summary={`实体记载与依据 · ${card.mentions.length} 项`}>
          {() =>
            card.mentions.map((item) => (
              <div className="dossier-related-entry" key={item.id}>
                <h4>{item.name}</h4>
                <p>{item.identity_basis || "未记录额外身份说明。"}</p>
                {refs(item.evidence_ids)}
              </div>
            ))
          }
        </ReadingDetails>
      )}
      <h3>关联事件</h3>
      {card.events.length ? (
        <ReadingDetails summary={`事件与参与者 · ${card.events.length} 项`}>
          {() =>
            card.events.map((item) => (
              <div className="dossier-related-entry" key={item.id}>
                <h4>
                  {entity(item.id)}
                  {dateLabel(item.date) && ` · ${dateLabel(item.date)}`}
                </h4>
                <p>{item.description}</p>
                <EventNature value={item} />
                {item.action && <p>行为：{item.action}</p>}
                {item.place?.value && <p>地点：{item.place.value}</p>}
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
                      {item.participant_ids.map((id) => (
                        <span key={id}>{entity(id)} </span>
                      ))}
                    </p>
                  )
                )}
                {refs(item.evidence_ids)}
              </div>
            ))
          }
        </ReadingDetails>
      ) : (
        <p className="dossier-missing">本卡未记录独立事件。</p>
      )}
      {!!card.relations.length && (
        <ReadingDetails
          summary={`本篇关系与依据 · ${card.relations.length} 项`}
        >
          {() =>
            card.relations.map((item) => (
              <div className="dossier-related-entry" key={item.id}>
                <p>
                  {entity(item.subject_id)}{" "}
                  <strong>{relationLabel(item.predicate)}</strong>{" "}
                  {entity(item.object_id)}
                  {item.role && ` · ${relationLabel(item.role)}`}
                </p>
                <p>{item.reason}</p>
                {refs(item.evidence_ids)}
              </div>
            ))
          }
        </ReadingDetails>
      )}
      <h3>关联资料</h3>
      {card.cross_relations?.length || card.derived_relations?.items?.length ? (
        <ReadingDetails summary="查看已有文献关系与双方依据">
          {() => (
            <>
              <CrossCardRelations
                relations={card.cross_relations ?? []}
                evidence={card.evidence}
                bookId={card.book_id}
              />
              <DerivedCardRelations relations={card.derived_relations} />
            </>
          )}
        </ReadingDetails>
      ) : (
        <p className="dossier-missing">
          尚无已记录的件级文献关系；不按共同主题自动建立关系。
        </p>
      )}
    </>
  );
}
