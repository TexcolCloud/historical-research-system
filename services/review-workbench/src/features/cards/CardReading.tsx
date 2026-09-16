import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { RenderedMarkdown } from "@/editor/RenderedMarkdown.tsx";
import type { components } from "@/client/schema.d.ts";

type Detail = components["schemas"]["CardDetail"];
type Item = components["schemas"]["DraftItem"];
const kindLabels: Record<Item["kind"], string> = {
  section: "研究概览",
  evidence: "原文证据",
  argument: "研究论证",
  issue: "待查问题",
  paper_use: "研究用途",
};
const stateLabels: Record<Item["epistemic_state"], string> = {
  stated: "来源陈述",
  inferred: "研究推断",
  unknown: "未知",
  undetermined: "尚未确定",
  not_found: "本次范围内未找到",
  disputed: "存在分歧",
  not_applicable: "不适用",
};
const relationLabels = {
  support: "支持",
  limit: "限制",
  counter: "反证",
  background: "背景",
};
const entityLabels = {
  person: "人物",
  organization: "机构",
  place: "地点",
  event: "事件",
};
const precisionLabels = {
  day: "日",
  month: "月",
  year: "年",
  range: "时间范围",
  unknown: "精度未明",
};

export default function CardReading({ card }: { card: Detail }) {
  const draft = card.candidate;
  const elements = useRef(new Map<string, HTMLElement>());
  const sourcePanel = useRef<HTMLElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const [selected, setSelected] = useState<{
    unitId: string;
    itemId: string;
    index?: number;
  } | null>(null);
  const source = card.units.find((unit) => unit.unit_id === selected?.unitId);
  const location = card.quote_locations?.find(
    (row) =>
      row.item_id === selected?.itemId &&
      row.selection_index === selected?.index,
  );
  const selection = draft.items.find(
    (item) => item.item_id === selected?.itemId,
  )?.selections?.[selected?.index ?? -1];
  // API offsets are Unicode code points, not JavaScript UTF-16 code units.
  const characters = source ? Array.from(source.text) : [];
  const located =
    location?.unit_id === selected?.unitId &&
    location?.start != null &&
    location.end != null &&
    location.start >= 0 &&
    location.end > location.start &&
    location.end <= characters.length &&
    characters.slice(location.start, location.end).join("") ===
      selection?.quote;
  const pages = located ? location.pages : [];

  useEffect(() => {
    if (!selected) return;
    sourcePanel.current?.scrollIntoView({ block: "nearest" });
    sourcePanel.current?.focus({ preventScroll: true });
  }, [selected]);

  function jump(identity: string) {
    const target = elements.current.get(identity);
    target?.scrollIntoView({ block: "center" });
    target?.focus({ preventScroll: true });
  }
  function references(identities: string[]) {
    return (
      <div className="platform-card-references">
        {identities.map((identity) => {
          const target = draft.items.find((item) => item.item_id === identity);
          return target ? (
            <Button
              key={identity}
              variant="link"
              onClick={() => jump(identity)}
            >
              {target.title}
            </Button>
          ) : (
            <span key={identity}>关联证据暂不可用</span>
          );
        })}
      </div>
    );
  }
  function openSource(
    event: React.MouseEvent<HTMLButtonElement>,
    unitId: string,
    itemId: string,
    index?: number,
  ) {
    opener.current = event.currentTarget;
    setSelected({ unitId, itemId, index });
  }
  function closeSource() {
    setSelected(null);
    opener.current?.scrollIntoView({ block: "nearest" });
    opener.current?.focus({ preventScroll: true });
  }

  return (
    <div className="platform-card-content" data-source-open={Boolean(selected)}>
      <article aria-label="史料卡详情">
        <header className="platform-card-heading">
          <span
            className="platform-status"
            data-state={card.state === "adopted" ? "ready" : "awaiting_review"}
          >
            {card.state === "adopted"
              ? "已通过机器核验"
              : "候选卡 · 尚未通过机器核验"}
          </span>
          <h1>{card.title}</h1>
          <p>
            {draft.document_type} · {draft.source_layer}
          </p>
          <a
            className="platform-export"
            href={`/api/v2/cards/${card.id}/export`}
          >
            导出卡片 Markdown
          </a>
          {draft.tags.length > 0 && (
            <div className="platform-card-tags" aria-label="主题标签">
              {draft.tags.map((tag, i) => (
                <span key={i}>{tag}</span>
              ))}
            </div>
          )}
        </header>
        <div className="platform-card-dates">
          {(
            [
              ["形成时间", draft.formation_date],
              ["事件时间", draft.event_date],
            ] as const
          ).map(([label, date]) => (
            <section key={label} aria-label={label}>
              <h2>{label}</h2>
              <p>
                {date.original ||
                  (date.start_year
                    ? `${date.start_year}${date.end_year && date.end_year !== date.start_year ? `—${date.end_year}` : ""}年`
                    : "未明确")}
              </p>
              <small>
                {precisionLabels[date.precision]} ·{" "}
                {stateLabels[date.state as Item["epistemic_state"]] ??
                  date.state}
              </small>
              {!!date.basis?.length && (
                <>
                  <span className="platform-card-label">时间依据</span>
                  {references(date.basis)}
                </>
              )}
            </section>
          ))}
        </div>
        {draft.entities.length > 0 && (
          <details className="platform-card-entities" open>
            <summary>
              相关人物、机构、地点与事件 · {draft.entities.length}
            </summary>
            <ul>
              {draft.entities.map((entity, i) => (
                <li key={i}>
                  <span className="platform-card-label">
                    {entityLabels[entity.kind]}
                  </span>
                  <strong>{entity.original_name}</strong>
                  {entity.identity_basis && (
                    <p>身份依据：{entity.identity_basis}</p>
                  )}
                  {references(entity.evidence_refs)}
                </li>
              ))}
            </ul>
          </details>
        )}
        <nav className="platform-card-outline" aria-label="卡片内容导航">
          {draft.items.map((item) => (
            <Button
              key={item.item_id}
              variant="link"
              onClick={() => jump(item.item_id)}
            >
              {item.title}
            </Button>
          ))}
        </nav>
        {draft.no_argument_reason && (
          <p className="platform-inline-status">
            未形成独立论证的原因：{draft.no_argument_reason}
          </p>
        )}
        {draft.items.map((item) => {
          const relations = draft.evidence_relations.filter(
            (row) =>
              row.argument_id === item.item_id ||
              row.evidence_id === item.item_id,
          );
          return (
            <section
              key={item.item_id}
              className="platform-card-item"
              aria-label={item.title}
              tabIndex={-1}
              ref={(element) => {
                if (element) elements.current.set(item.item_id, element);
                else elements.current.delete(item.item_id);
              }}
            >
              <div className="platform-card-item-meta">
                <span>{kindLabels[item.kind]}</span>
                <span data-state={item.epistemic_state}>
                  {stateLabels[item.epistemic_state]}
                </span>
              </div>
              <h2>{item.title}</h2>
              {item.section && item.section !== item.title && (
                <p className="platform-card-label">{item.section}</p>
              )}
              <p className="platform-card-attribution">
                <strong>陈述归属：</strong>
                {item.attribution || "未明确，请结合原文判断"}
              </p>
              {item.text && <RenderedMarkdown markdown={item.text} />}
              {item.selections?.map((quote, index) => (
                <blockquote key={index}>
                  <RenderedMarkdown markdown={quote.quote} />
                  <Button
                    variant="link"
                    onClick={(event) =>
                      openSource(event, quote.unit_id, item.item_id, index)
                    }
                  >
                    定位引文 {index + 1} · 查看出处
                  </Button>
                </blockquote>
              ))}
              {(
                [
                  ["释读", item.interpretation],
                  ["上下文", item.context],
                  ["表格口径与释读", item.table_reading],
                ] as const
              ).map(
                ([label, value]) =>
                  value && (
                    <div key={label} className="platform-card-explanation">
                      <h3>{label}</h3>
                      <RenderedMarkdown markdown={value} />
                    </div>
                  ),
              )}
              {(
                [
                  ["限制条件", item.limitations],
                  ["其他解释", item.alternatives],
                  ["待查问题", item.questions],
                ] as const
              ).map(
                ([label, values]) =>
                  !!values?.length && (
                    <section
                      key={label}
                      className="platform-card-explanation"
                      aria-label={label}
                    >
                      <h3>{label}</h3>
                      <ul>
                        {values.map((value, i) => (
                          <li key={i}>
                            <RenderedMarkdown markdown={value} />
                          </li>
                        ))}
                      </ul>
                    </section>
                  ),
              )}
              {!!item.evidence_refs?.length && (
                <div className="platform-card-explanation">
                  <h3>引用证据</h3>
                  {references(item.evidence_refs)}
                </div>
              )}
              {relations.length > 0 && (
                <section
                  className="platform-card-relations"
                  aria-label="证据与论证关系"
                >
                  <h3>证据与论证关系</h3>
                  <ul>
                    {relations.map((relation, i) => (
                      <li key={i}>
                        <span className="platform-status">
                          {relationLabels[relation.role]}
                        </span>
                        {references([
                          relation.argument_id === item.item_id
                            ? relation.evidence_id
                            : relation.argument_id,
                        ])}
                        <p>{relation.reason}</p>
                        <p>
                          <strong>适用范围：</strong>
                          {relation.used_scope || "未明确"}
                        </p>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {!!item.source_unit_ids?.length && (
                <details className="platform-card-source-list">
                  <summary>关联来源正文</summary>
                  {item.source_unit_ids.map((identity) => (
                    <Button
                      key={identity}
                      variant="link"
                      onClick={(event) =>
                        openSource(event, identity, item.item_id)
                      }
                    >
                      {card.units.find((unit) => unit.unit_id === identity)
                        ?.title ?? "来源暂不可用"}
                    </Button>
                  ))}
                </details>
              )}
            </section>
          );
        })}
        <details className="platform-card-verdict">
          <summary>机器核验记录（技术明细）</summary>
          <pre>{JSON.stringify(card.verdict, null, 2)}</pre>
        </details>
      </article>
      {selected && (
        <aside
          className="platform-card-source"
          aria-label="出处对照"
          tabIndex={-1}
          ref={sourcePanel}
        >
          <div className="platform-review-actions">
            <h2>{source?.title ?? "来源正文暂不可用"}</h2>
            <Button variant="ghost" onClick={closeSource}>
              收起出处
            </Button>
          </div>
          {selected.index != null && (
            <>
              <p className="platform-card-label">
                引文 {selected.index + 1} ·{" "}
                {pages.length
                  ? `PDF 物理页 ${pages.join("、")}`
                  : "原件页码未能精确定位"}
              </p>
              {(!located || location?.issue) && (
                <p role="alert">
                  {location?.issue || "暂无法精确定位此引文，请查看来源全文。"}
                </p>
              )}
              {located && (
                <div className="platform-card-quote-context">
                  <h3>原始文本定位</h3>
                  <p className="platform-card-label">
                    高亮当前引文，保留 Markdown 原始字符；排版正文可在下方展开。
                  </p>
                  <pre>
                    {location.start! > 160 && "…"}
                    {characters
                      .slice(
                        Math.max(0, location.start! - 160),
                        location.start!,
                      )
                      .join("")}
                    <mark>
                      {characters
                        .slice(location.start!, location.end!)
                        .join("")}
                    </mark>
                    {characters
                      .slice(location.end!, location.end! + 160)
                      .join("")}
                    {location.end! + 160 < characters.length && "…"}
                  </pre>
                </div>
              )}
            </>
          )}
          {source && (
            <>
              {pages.length > 0 && (
                <div
                  className="platform-card-page-links"
                  aria-label="引文原书页码"
                >
                  {pages.map((page) => (
                    <a
                      key={page}
                      target="_blank"
                      rel="noreferrer"
                      href={`/api/v2/runs/${source.run_id}/artifacts/original.pdf#page=${page}`}
                    >
                      对照原书 · 第 {page} 页
                    </a>
                  ))}
                </div>
              )}
              <details
                key={`${selected.itemId}:${selected.index ?? "full"}`}
                open={selected.index == null}
                className="platform-card-full-source"
              >
                <summary>
                  完整来源正文 · PDF 物理页{" "}
                  {source.pages.join("、") || "未记录"}
                </summary>
                <RenderedMarkdown
                  markdown={source.text}
                  assetBaseUrl={`/api/v2/runs/${source.run_id}/artifacts`}
                />
                <div className="platform-card-page-links">
                  {source.pages.map((page) => (
                    <a
                      key={page}
                      target="_blank"
                      rel="noreferrer"
                      href={`/api/v2/runs/${source.run_id}/artifacts/original.pdf#page=${page}`}
                    >
                      来源原件 · 第 {page} 页
                    </a>
                  ))}
                </div>
              </details>
            </>
          )}
        </aside>
      )}
    </div>
  );
}
