import { useState, type ReactNode } from "react";
import { isCardOnly } from "./api";
import { sourceItemLabels } from "./labels";
import {
  authorLabel,
  cardNumber,
  certaintyLabels,
  dateLabel,
  pageRange,
  type ReadingCard,
  type SourceField,
} from "./card-reading";

type Refs = (ids: string[]) => ReactNode;
export function ReadingDetails({
  summary,
  children,
}: {
  summary: ReactNode;
  children: () => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="dossier-secondary"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>{summary}</summary>
      {open && children()}
    </details>
  );
}
export const cardChapters = [
  ["basic", "基本资料"],
  ["context", "内容说明"],
  ["core", "核心内容"],
  ["boundaries", "性质、价值与边界"],
  ["arguments", "可用论证单元"],
  ["related", "关联实体与资料"],
  ["citation", "规范引注与页码"],
  ["notes", "我的备注"],
] as const;

function SourceReading({
  title,
  value,
  refs,
  fallback = "本卡尚未记录该项。",
}: {
  title: string;
  value?: SourceField;
  refs: Refs;
  fallback?: string;
}) {
  return (
    <div className="dossier-reading-field">
      <h3>{title}</h3>
      <p className={value?.value ? "research-preserve" : "dossier-missing"}>
        {value?.value || fallback}
      </p>
      {value?.certainty &&
        value.certainty !== "stated" &&
        value.certainty !== "unknown" && (
          <p className="research-provenance">
            {certaintyLabels[value.certainty]}
          </p>
        )}
      {value?.note && <p className="research-provenance">{value.note}</p>}
      {refs(value?.evidence_ids ?? [])}
    </div>
  );
}

export function CardBasic({ card, refs }: { card: ReadingCard; refs: Refs }) {
  const pages = card.pages?.length
    ? card.pages
    : card.evidence
        .filter((item) => item.article_id === card.article_id)
        .flatMap((item) => item.pages);
  return (
    <>
      <dl className="dossier-basic-grid">
        <div>
          <dt>卡片编号</dt>
          <dd>
            {cardNumber(card)}
            <small>本系统显示编号，非档案馆档号</small>
          </dd>
        </div>
        <div>
          <dt>题名依据</dt>
          <dd>
            {
              {
                source: "原文题名",
                supplied: "已有编目",
                system: "系统拟题",
                unknown: "未明",
              }[card.metadata?.title_origin || "unknown"]
            }
          </dd>
        </div>
        <div>
          <dt>作者 / 形成者</dt>
          <dd>
            {authorLabel(card)}
            {refs(card.metadata?.authors?.evidence_ids ?? [])}
          </dd>
        </div>
        <div>
          <dt>形成日期</dt>
          <dd>
            {dateLabel(card.formation_date) || "形成日期未详"}
            {refs(card.formation_date?.evidence_ids ?? [])}
          </dd>
        </div>
        {card.event_date?.text && (
          <div>
            <dt>所记事件日期</dt>
            <dd>
              {dateLabel(card.event_date)}
              {refs(card.event_date.evidence_ids ?? [])}
            </dd>
          </div>
        )}
        <div>
          <dt>文献类型</dt>
          <dd>{card.document_type || "未记录"}</dd>
        </div>
        <div>
          <dt>史料层次</dt>
          <dd>{card.source_layer || "未记录"}</dd>
        </div>
        <div>
          <dt>
            {card.pages?.length ? "文献 PDF 物理页" : "已关联引文 PDF 页"}
          </dt>
          <dd>{pageRange(pages) || "页码尚未可靠关联"}</dd>
        </div>
        <div>
          <dt>原书印刷页</dt>
          <dd>
            {card.printed_pages ||
              card.dossier?.citation.printed_pages.value ||
              "原书页码尚未记录"}
          </dd>
        </div>
        <div>
          <dt>收录来源</dt>
          <dd>{card.book_title || "来源书名未记录"}</dd>
        </div>
      </dl>
      {!card.metadata && card.tags.length > 0 && (
        <p className="research-provenance">
          已有主题词：{card.tags.join("、")}
        </p>
      )}
      {card.metadata && (
        <details className="dossier-secondary">
          <summary>其他编目与形成信息</summary>
          <p className="research-provenance">
            已有主题词：{card.tags.join("、") || "未记录"}
          </p>
          <dl className="dossier-basic-grid">
            {Object.entries({
              original_title: "原题",
              recipients: "收受者",
              editors: "编者或整理者",
              publication: "出版信息",
              volume: "卷次",
              archive_id: "原始档号",
              formation_place: "成文地点",
              purpose: "写作目的",
              testimony: "亲历或转述性质",
            }).map(([key, label]) => {
              const value =
                card.metadata?.[
                  key as Exclude<
                    keyof NonNullable<ReadingCard["metadata"]>,
                    "title_origin"
                  >
                ];
              return (
                <div key={key}>
                  <dt>{label}</dt>
                  <dd>
                    {value?.value || "未详"}
                    {value?.note && (
                      <p className="research-provenance">{value.note}</p>
                    )}
                    {refs(value?.evidence_ids ?? [])}
                  </dd>
                </div>
              );
            })}
          </dl>
        </details>
      )}
    </>
  );
}

export function CardContext({ card, refs }: { card: ReadingCard; refs: Refs }) {
  return (
    <>
      <p className="dossier-layer-label">
        {card.human_decision?.action === "edit"
          ? "用户修订 · 来源阅读说明"
          : "模型整理 · 来源阅读说明"}
      </p>
      <p className="dossier-lead research-preserve">{card.summary}</p>
      {card.dossier ? (
        <>
          <SourceReading
            title="研究对象与时空范围"
            value={card.dossier.research_scope}
            refs={refs}
          />
          <SourceReading
            title="形成背景"
            value={card.dossier.formation_background}
            refs={refs}
          />
          <SourceReading
            title="文本结构"
            value={card.dossier.text_structure}
            refs={refs}
          />
        </>
      ) : (
        <p className="dossier-missing">
          原有卡片未单列研究对象、形成背景和文本结构；请结合提要和原文阅读。
        </p>
      )}
    </>
  );
}

export function CardCore({ card, refs }: { card: ReadingCard; refs: Refs }) {
  const labels: Record<string, string> = isCardOnly(card)
    ? { ...sourceItemLabels, argument: "原作者观点" }
    : {
        section: "内容层次",
        evidence: "论据",
        argument: "论证",
        issue: "问题",
        paper_use: "旧流程论文用途",
      };
  const items = card.items.filter(
    (item) => !isCardOnly(card) || item.kind !== "paper_use",
  );
  return (
    <>
      {!items.length && (
        <p className="dossier-missing">
          本卡尚无独立内容条目，请先阅读上方提要及原文。
        </p>
      )}
      {items.map((item) => (
        <section className="dossier-topic" key={item.id}>
          <p className="dossier-layer-label">
            {labels[item.kind] || item.kind}
            {item.epistemic_state && item.epistemic_state !== "stated"
              ? ` · ${certaintyLabels[item.epistemic_state] ?? item.epistemic_state}`
              : ""}
          </p>
          <h3>{item.title || "未命名内容要点"}</h3>
          <p className="research-preserve">{item.text}</p>
          {item.attribution && (
            <p className="research-provenance">观点归属：{item.attribution}</p>
          )}
          {refs(item.evidence_ids)}
          {!!item.limitations?.length && (
            <p className="dossier-item-limit">
              适用边界：{item.limitations.join("；")}
            </p>
          )}
        </section>
      ))}
    </>
  );
}

export function CardBoundaries({
  card,
  refs,
}: {
  card: ReadingCard;
  refs: Refs;
}) {
  return (
    <>
      {card.dossier ? (
        <>
          <SourceReading
            title="史料性质"
            value={card.dossier.source_nature}
            refs={refs}
          />
          <SourceReading
            title="来源角色"
            value={card.dossier?.source_role}
            refs={refs}
          />
          <SourceReading
            title="史料价值"
            value={card.dossier?.research_value}
            refs={refs}
          />
          <div className="dossier-boundary-grid">
            <SourceReading
              title="可以支持"
              value={card.dossier?.can_support}
              refs={refs}
              fallback="未单独记录可以支持的判断，不能仅由提要推定。"
            />
            <SourceReading
              title="不能直接证明"
              value={card.dossier?.cannot_support}
              refs={refs}
            />
            <SourceReading
              title="仍需互证"
              value={card.dossier?.needs_corroboration}
              refs={refs}
            />
          </div>
        </>
      ) : (
        <p className="dossier-missing">
          原有卡片未分别整理史料性质、来源角色、史料价值与证据三分边界。已知文献类型和层次见基本资料，原有使用限制保留如下。
        </p>
      )}
      <h3>已有使用边界</h3>
      {card.limitations.length ? (
        <ul>
          {card.limitations.map((value, index) => (
            <li key={index}>{value}</li>
          ))}
        </ul>
      ) : (
        <p className="dossier-missing">未记录额外边界，不表示没有使用限制。</p>
      )}
      {!isCardOnly(card) && (
        <>
          {card.no_argument_reason && <p>{card.no_argument_reason}</p>}
          {!!card.alternatives.length && (
            <>
              <h3>旧流程备选解释</h3>
              <ul>
                {card.alternatives.map((text, index) => (
                  <li key={index}>{text}</li>
                ))}
              </ul>
            </>
          )}
          {!!card.questions.length && (
            <>
              <h3>旧流程待解决问题</h3>
              <ul>
                {card.questions.map((text, index) => (
                  <li key={index}>{text}</li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </>
  );
}

export function CardArguments({
  card,
  refs,
}: {
  card: ReadingCard;
  refs: Refs;
}) {
  return (
    <>
      <p className="research-provenance">
        以下是带原文依据的阅读整理，不替代研究者的论证与史实互证。
      </p>
      {card.dossier?.argument_units.length ? (
        card.dossier.argument_units.map((unit) => (
          <section className="dossier-topic" key={unit.id}>
            <h3>{unit.title}</h3>
            <p>{unit.claim}</p>
            {refs(unit.evidence_ids)}
            <p>适用边界：{unit.applicability_limit}</p>
            <p>仍需互证：{unit.corroboration_needed}</p>
          </section>
        ))
      ) : (
        <p className="dossier-missing">
          {card.dossier?.argument_units_note ||
            "本卡未制作独立论证单元。原作者的观点保留在核心内容中。"}
        </p>
      )}
    </>
  );
}

export function CardCitation({
  card,
  refs,
}: {
  card: ReadingCard;
  refs: Refs;
}) {
  const citation = card.dossier?.citation;
  const knownCitation = citation?.full.value || card.source_citation;
  const fullCitationTitle =
    citation?.completeness === "complete"
      ? "完整引注"
      : knownCitation
        ? "已知出处（尚未齐备）"
        : "出处未详";
  return (
    <>
      <SourceReading
        title={fullCitationTitle}
        value={citation?.full}
        refs={refs}
        fallback={
          card.source_citation ||
          "本卡尚未记录规范完整引注，请核对原始书目信息。"
        }
      />
      {citation && (
        <SourceReading title="后续简称" value={citation.short} refs={refs} />
      )}
      <p>
        原书印刷页：
        {citation?.printed_pages.value || card.printed_pages || "未记录"} ·
        出版日期：
        {dateLabel(citation?.publication_date || card.publication_date) ||
          "未详"}
      </p>
      {citation?.completeness !== "complete" && (
        <p className="dossier-missing">
          引注信息尚未齐备
          {citation?.missing_fields.length
            ? `：${citation.missing_fields.join("、")}`
            : "，请核对出版与页码信息。"}
        </p>
      )}
    </>
  );
}
