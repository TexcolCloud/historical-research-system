import { Link } from "react-router-dom";
import type { components } from "@/client/schema";
import Evidence from "./ResearchEvidence";
import QuestionPicker from "./QuestionPicker";
import { relationLabel } from "./relation-labels";

export default function DerivedCardRelations({
  relations,
}: {
  relations?: components["schemas"]["ResearchDerivedRelationsResponse"];
}) {
  if (!relations?.items?.length) return null;
  return (
    <section className="research-derived-relations">
      <h3>跨卡关联与后续补证</h3>
      <p className="research-provenance">
        由后续独立核验的材料建立；双方卡片与保留的证据包分别可查。
      </p>
      {relations.items.map((relation) => (
        <article className="research-relation" key={relation.id}>
          <h4>{relationLabel(relation.predicate, relation.kind)}</h4>
          <p>{relation.reason}</p>
          {relation.source_current !== false &&
            relation.target_current !== false && (
              <QuestionPicker
                material={{
                  kind: "relation",
                  target_id: relation.id,
                  title: relationLabel(relation.predicate, relation.kind),
                }}
              />
            )}
          {(relation.source_current === false ||
            relation.target_current === false) && (
            <p className="workspace-status-note">
              关系的一端已有新版本，请重新核验后引用。
            </p>
          )}
          <div className="research-decision-actions">
            <Link
              to={`/research?card=${encodeURIComponent(relation.source_id)}`}
            >
              核对关系起点材料
            </Link>
            <Link
              to={`/research?card=${encodeURIComponent(relation.target_id)}`}
            >
              核对关系终点材料
            </Link>
            <Link to={`/research?card=${encodeURIComponent(relation.card_id)}`}>
              查看独立核验的关系证据包
            </Link>
          </div>
          <details>
            <summary>对照关系保留引文</summary>
            <Evidence
              evidence={(relation.evidence ?? []).map((item) => ({
                ...item,
                pages: item.pages ?? [],
                footnote_ids: item.footnote_ids ?? [],
              }))}
              bookId={relation.book_id}
            />
          </details>
        </article>
      ))}
      {relations.truncated && (
        <p className="research-provenance">
          关系较多，当前显示前 100 项，可沿相关材料继续追查。
        </p>
      )}
    </section>
  );
}
