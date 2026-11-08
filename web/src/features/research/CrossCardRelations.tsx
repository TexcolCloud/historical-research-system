import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import type { components } from "@/client/schema";
import { research, researchKeys, type ResearchEvidence } from "./api";
import Evidence from "./ResearchEvidence";
import { relationLabel } from "./relation-labels";
function Relation({
  relation,
  evidence,
  bookId,
}: {
  relation: components["schemas"]["CrossCardRelation"];
  evidence: ResearchEvidence[];
  bookId: string;
}) {
  const [open, setOpen] = useState(false);
  const target = useQuery({
    queryKey: researchKeys.card(relation.target_card_id),
    queryFn: ({ signal }) => research.card(relation.target_card_id, signal),
    enabled: open,
    staleTime: 30000,
    retry: false,
  });
  const exact =
    target.data &&
    target.data.version === relation.target_card_version &&
    target.data.publication_sha256 === relation.target_publication_sha256;
  return (
    <article className="research-relation">
      <h4>
        {relationLabel(relation.kind)}
        {relation.certainty === "disputed"
          ? " · 有争议"
          : relation.certainty === "inferred"
            ? " · 推断"
            : " · 原文记载"}
      </h4>
      <p>{relation.reason}</p>
      <div className="research-decision-actions">
        <Button
          variant="outline"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          {open ? "收起双方证据" : "对照双方证据"}
        </Button>
        <Link
          to={`/research?card=${encodeURIComponent(relation.target_card_id)}`}
        >
          打开对方史料卡
        </Link>
      </div>
      {open && (
        <div className="research-cross-evidence">
          <Evidence
            evidence={evidence.filter((item) =>
              relation.source_evidence_ids.includes(item.id),
            )}
            bookId={bookId}
          />
          {target.isPending ? (
            <p role="status">正在读取对方保留证据…</p>
          ) : target.isError ? (
            <p role="alert">
              {target.error.message}
              <Button onClick={() => void target.refetch()}>
                重新读取对方证据
              </Button>
            </p>
          ) : (
            target.data && (
              <section>
                <h4>{target.data.title}</h4>
                {!exact ? (
                  <p className="workspace-status-note">
                    对方卡片版本已有变化，不能把当前内容当作旧关系的依据。请更新关联核验后引用。
                  </p>
                ) : (
                  <Evidence
                    evidence={target.data.evidence.filter((item) =>
                      relation.target_evidence_ids.includes(item.id),
                    )}
                    bookId={target.data.book_id}
                    historical={
                      target.data.source_current === false ||
                      target.data.superseded
                    }
                  />
                )}
              </section>
            )
          )}
        </div>
      )}
    </article>
  );
}
export default function CrossCardRelations({
  relations,
  evidence,
  bookId,
}: {
  relations: components["schemas"]["CrossCardRelation"][];
  evidence: ResearchEvidence[];
  bookId: string;
}) {
  if (!relations.length) return null;
  return (
    <section className="research-cross-relations">
      <h3>跨卡材料关系</h3>
      <p className="research-provenance">
        关系两端各自保留版本与证据；转载和同源材料不作为独立互证。
      </p>
      {relations.map((item) => (
        <Relation
          key={item.id}
          relation={item}
          evidence={evidence}
          bookId={bookId}
        />
      ))}
    </section>
  );
}
