import type { ReactNode } from "react";
import type { ReadingCard } from "./card-reading";
import { eventNatureLabels } from "./EventNature";
import { relationLabel } from "./relation-labels";

/** Expand retained identifiers for reading; never rewrite a verification verdict. */
export default function CardReviewIssues({
  card,
  renderEvidence,
}: {
  card: ReadingCard;
  renderEvidence: (ids: string[]) => ReactNode;
}) {
  const checks = card.checks;
  const issues = checks?.issues ?? [];
  const assessments =
    checks?.coverage_assessments?.filter((item) => item.status === "missing") ??
    [];
  if (!issues.length && !assessments.length) return null;
  const objects = [
    ...card.items.map((item) => ({ ...item, name: item.title })),
    ...(card.mentions ?? []),
    ...(card.events ?? []),
    ...card.relations.map((item) => ({
      ...item,
      name: relationLabel(item.predicate),
    })),
  ];
  const labels = new Map<string, string>([
    ...Object.entries(eventNatureLabels),
    ["participant_ids", "参与者"],
    ["participant_roles", "参与者角色"],
    ["evidence_ids", "引文依据"],
    ["place-kind", "地点类别"],
    ["mention", "实体提及"],
    ["finding", "核验项目"],
    ...(checks?.checked_inventory_ids ?? []).map(
      (id, index): [string, string] => [id, `核验项目${index + 1}`],
    ),
  ]);
  const names = new Map<string, (typeof objects)[number]>();
  const citations = new Map<string, string>();
  const localId =
    "local_id" in card && typeof card.local_id === "string"
      ? card.local_id
      : card.id;
  const variants = (id: string) => [id, `${card.id}/${id}`, `${localId}/${id}`];
  for (const object of objects)
    for (const id of variants(object.id)) {
      names.set(id, object);
      labels.set(id, `“${object.name}”`);
    }
  card.evidence.forEach((evidence, index) => {
    for (const id of variants(evidence.id)) {
      labels.set(id, `引文${index + 1}`);
      citations.set(id, evidence.id);
    }
  });
  const pattern = new RegExp(
    `(?<![A-Za-z0-9_/-])(${[...labels.keys()]
      .sort((a, b) => b.length - a.length)
      .map((id) => id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .join("|")}|[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)+)(?![A-Za-z0-9_/-])`,
    "g",
  );
  const findings = (
    issues.length ? issues : assessments.map((item) => item.reason)
  ).map((text) => {
    const related = new Set<(typeof objects)[number]>();
    const evidenceIds = new Set<string>();
    const readable = text
      .split(/(“[^”]*”|「[^」]*」|"[^"\n]*")/g)
      .map((part, index) =>
        index % 2
          ? part
          : part.replace(pattern, (identifier) => {
              // Some retained reports join local citations as e5/e11. Only
              // expand a list when every member is a citation in this card.
              const ids = identifier.split("/");
              const keys = labels.has(identifier)
                ? [identifier]
                : ids.every((id) => citations.has(id))
                  ? ids
                  : [];
              return (
                keys
                  .map((id) => {
                    const object = names.get(id);
                    if (object) {
                      related.add(object);
                      object.evidence_ids?.forEach((key) =>
                        evidenceIds.add(key),
                      );
                    }
                    const evidence = citations.get(id);
                    if (evidence) evidenceIds.add(evidence);
                    return labels.get(id);
                  })
                  .join("、") || identifier
              );
            }),
      )
      .join("");
    return {
      readable,
      names: [...related].map((item) => item.name),
      evidenceIds: [...evidenceIds],
    };
  });
  return (
    <div className="research-verification-issues">
      <section aria-label="疑点说明">
        <h3>需要核对的问题</h3>
        <p>
          对照下方引文和原图。内容有误时选择“修订完整卡片”，确认后作出最终决定。原始核验记录保留在下方。
        </p>
        {findings.map((finding, index) => (
          <article key={index}>
            <h4>
              {`疑点 ${index + 1}`}
              {finding.names[0] && ` · ${finding.names[0]}`}
            </h4>
            <p>{finding.readable}</p>
            {renderEvidence(finding.evidenceIds)}
          </article>
        ))}
      </section>
      <details>
        <summary>原始核验记录</summary>
        <ul>
          {issues.map((issue, index) => (
            <li key={index}>{issue}</li>
          ))}
        </ul>
        {!issues.length &&
          assessments.map((item, index) => <p key={index}>{item.reason}</p>)}
      </details>
    </div>
  );
}
