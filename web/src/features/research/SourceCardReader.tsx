import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import CardEditor from "./CardEditor";
import CardNotes from "./CardNotes";
import CardReviewIssues from "./CardReviewIssues";
import Evidence from "./ResearchEvidence";
import EvidenceOriginal from "./EvidenceOriginal";
import CardRelatedSources from "./CardRelatedSources";
import {
  CardArguments,
  CardBasic,
  CardBoundaries,
  CardCitation,
  CardContext,
  CardCore,
  ReadingDetails,
  cardChapters,
} from "./CardDossierSections";
import {
  authorLabel,
  cardNumber,
  cardStateLabel,
  dateLabel,
  pageRange,
  type ReadingCard,
} from "./card-reading";
import { isCardOnly, research, researchKeys } from "./api";
import { useResearchPendingNavigation } from "./pending-navigation";

export default function SourceCardReader({
  card,
  notice,
}: {
  card: ReadingCard;
  notice: ReactNode;
}) {
  const scope = useId();
  const query = useQueryClient();
  const pendingNavigation = useResearchPendingNavigation();
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<string>();
  const [activeChapter, setActiveChapter] = useState<string>("basic");
  const [editing, setEditing] = useState(false),
    [fullEditing, setFullEditing] = useState(false);
  const [text, setText] = useState(card.summary);
  const returnFocus = useRef<HTMLButtonElement | null>(null);
  const returnChapter = useRef<string | undefined>(undefined);
  const evidencePanel = useRef<HTMLElement>(null);
  const historical =
    card.source_current === false ||
    card.source_status === "stale" ||
    card.research_current === false ||
    card.superseded === true;
  const checking =
    params.get("cardMode") === "review" ||
    (params.get("from") === "pending" && params.get("cardMode") !== "read");
  const selectedIndex = card.evidence.findIndex((item) => item.id === selected);
  const coverage = card.coverage ?? [];
  const selectedEvidence =
    selectedIndex < 0 ? undefined : card.evidence[selectedIndex];
  useEffect(() => {
    if (selectedEvidence)
      evidencePanel.current?.scrollIntoView?.({ block: "nearest" });
  }, [selectedEvidence?.id]);
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
      for (const key of [
        researchKeys.book(saved.book_id),
        researchKeys.cards(saved.book_id),
        researchKeys.run(saved.run_id),
        ["research", "search"],
        ["research", "entity"],
      ])
        void query.invalidateQueries({ queryKey: key });
      void pendingNavigation.advance({ cardId: saved.id });
    },
  });
  function openEvidence(id: string, button: HTMLButtonElement) {
    returnFocus.current = button;
    returnChapter.current = button.closest(".dossier-chapter")?.id;
    setSelected(id);
  }
  function refs(ids: string[]) {
    return (
      <span className="research-evidence-refs">
        {ids.map((id) => {
          const index = card.evidence.findIndex((item) => item.id === id);
          if (index < 0) return null;
          const item = card.evidence[index];
          return (
            <button
              key={id}
              aria-label={`证据 ${index + 1}`}
              onClick={(event) => openEvidence(id, event.currentTarget)}
            >
              引文 {String(index + 1).padStart(2, "0")}
              {item.pages.length
                ? ` · PDF ${pageRange(item.pages)}`
                : " · 位置待核对"}
            </button>
          );
        })}
      </span>
    );
  }
  function entity(id: string) {
    const item = [...card.mentions, ...card.events].find(
      (item) => item.id === id,
    );
    if (!item)
      return (
        <span>{card.items.find((item) => item.id === id)?.title || id}</span>
      );
    return (
      <button
        className="research-inline-link"
        onClick={() => {
          const next = new URLSearchParams(params);
          next.delete("card");
          next.delete("cardMode");
          next.set("entity", item.entity_id || id);
          setParams(next);
        }}
      >
        {item.name}
      </button>
    );
  }
  function mode(value: "read" | "review") {
    const next = new URLSearchParams(params);
    next.set("cardMode", value);
    setParams(next, { replace: true });
  }
  function chapter(id: string) {
    setActiveChapter(id);
    document
      .getElementById(`${scope}-${id}`)
      ?.scrollIntoView?.({ block: "start" });
  }
  function section(id: string, title: string, children: ReactNode) {
    return (
      <section
        id={`${scope}-${id}`}
        aria-label={title}
        className="dossier-chapter"
      >
        <h2>{title}</h2>
        {children}
      </section>
    );
  }
  if (fullEditing)
    return (
      <CardEditor cardId={card.id} onClose={() => setFullEditing(false)} />
    );
  return (
    <article
      className="research-card-detail dossier-document"
      data-document-card={card.id}
      data-mode={checking ? "review" : "read"}
    >
      {params.get("from") === "pending" && (
        <Link className="research-source-link" to="/reviews">
          返回待办
        </Link>
      )}
      {notice}
      <header className="dossier-document-header">
        <div>
          <p className="dossier-identity">
            文献史料卡 <span>{cardNumber(card)}</span>
            <a
              className="research-source-link"
              href={research.exportHref(card.id)}
              download
            >
              {historical ? "导出历史卡片" : "导出 Markdown"}
            </a>
          </p>
          <h2>{card.title}</h2>
          <div className="dossier-byline">
            <span>{authorLabel(card)}</span>
            <span>形成：{dateLabel(card.formation_date) || "日期未详"}</span>
            <span className="research-status" data-state={card.state}>
              {historical
                ? `历史状态：${cardStateLabel(card.state)}`
                : cardStateLabel(card.state)}
            </span>
          </div>
          <p className="research-provenance">
            {card.book_title || "来源书名未记录"}
            {card.pages?.length ? ` · PDF 物理页 ${pageRange(card.pages)}` : ""}
            {card.printed_pages ? ` · 原书页 ${card.printed_pages}` : ""}
          </p>
        </div>
      </header>
      <div className="dossier-mode-bar">
        <div role="group" aria-label="卡片模式">
          <Button
            variant={checking ? "ghost" : "outline"}
            aria-pressed={!checking}
            onClick={() => mode("read")}
          >
            阅读本卡
          </Button>
          <Button
            variant={checking ? "outline" : "ghost"}
            aria-pressed={checking}
            onClick={() => mode("review")}
          >
            {historical ? "查看历史核验" : "核对本卡"}
          </Button>
        </div>
        <span>
          {checking
            ? "核对模式 · 对照原文后作出决定"
            : "阅读模式 · 一份完整文献，一张卡片"}
        </span>
      </div>
      {checking && (
        <section
          className="research-human-decision dossier-review"
          aria-label="人工决策"
        >
          <h2>核对与决定</h2>
          {historical ? (
            <p>历史记录保留原有判断，不能向旧来源提交当前决定。</p>
          ) : (
            <>
              <CardReviewIssues card={card} renderEvidence={refs} />
              {card.state === "human_accepted" ? (
                <p>你的决定已保存，原有证据与版本继续保留。</p>
              ) : card.state === "excluded" ? (
                <p>此卡已排除，原有证据和版本继续保留。</p>
              ) : card.state !== "pending" ? (
                <p>已完成原文核验，引用前可继续核对原文与使用边界。</p>
              ) : (
                <>
                  <p>
                    请点引文对照原文或原图，再决定是否采用。模型核验结果不代表你的确认。
                  </p>
                  <p>
                    人物、机构、地点、事件或引注需要改动时，请选择“修订完整卡片”；“修订提要”只修改提要。
                  </p>
                  {editing ? (
                    <>
                      <label htmlFor={`${scope}-summary`}>修订提要</label>
                      <textarea
                        id={`${scope}-summary`}
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
              <Button
                variant="outline"
                className="research-card-edit-control"
                onClick={() => setFullEditing(true)}
              >
                修订完整卡片
              </Button>
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
                onClick={() =>
                  void query.invalidateQueries({
                    queryKey: researchKeys.card(card.id),
                  })
                }
              >
                重新读取当前卡片
              </Button>
              {editing && " 当前修订草稿仍保留，请对照新版本后再提交。"}
            </p>
          )}
        </section>
      )}
      <nav className="dossier-chapter-nav" aria-label="本卡章节">
        {cardChapters.map(([id, label], index) => (
          <button
            key={id}
            aria-current={activeChapter === id ? "location" : undefined}
            onClick={() => chapter(id)}
          >
            <span>{String(index + 1).padStart(2, "0")}</span>
            {label}
          </button>
        ))}
      </nav>
      <div
        className="dossier-layout"
        data-evidence-open={Boolean(selectedEvidence)}
      >
        <div className="dossier-reading">
          {section("basic", "基本资料", <CardBasic card={card} refs={refs} />)}
          {section(
            "context",
            "内容说明",
            <CardContext card={card} refs={refs} />,
          )}
          {section("core", "核心内容", <CardCore card={card} refs={refs} />)}
          {section(
            "boundaries",
            "性质、价值与边界",
            <CardBoundaries card={card} refs={refs} />,
          )}
          {section(
            "arguments",
            "可用论证单元",
            <CardArguments card={card} refs={refs} />,
          )}
          {section(
            "related",
            "关联实体与资料",
            <CardRelatedSources card={card} entity={entity} refs={refs} />,
          )}
          {section(
            "citation",
            "规范引注与页码",
            <>
              <CardCitation card={card} refs={refs} />
              <ReadingDetails
                summary={`引文与页码索引 · ${card.evidence.length} 条`}
              >
                {() => (
                  <ol className="dossier-evidence-index">
                    {card.evidence.map((item, index) => (
                      <li key={item.id}>
                        <button
                          onClick={(event) =>
                            openEvidence(item.id, event.currentTarget)
                          }
                          aria-label={`查看证据 ${index + 1}`}
                        >
                          引文 {String(index + 1).padStart(2, "0")} · PDF 物理页{" "}
                          {pageRange(item.pages) || "位置待核对"}
                        </button>
                        <span>
                          {item.footnote_ids.length
                            ? `关联注释 ${item.footnote_ids.length} 项`
                            : ""}
                        </span>
                      </li>
                    ))}
                  </ol>
                )}
              </ReadingDetails>
              {!!coverage.length && (
                <ReadingDetails
                  summary={`核验记录与全文覆盖 · ${coverage.length} 段`}
                >
                  {() =>
                    coverage.map((unit) => (
                      <div key={unit.id}>
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
                      </div>
                    ))
                  }
                </ReadingDetails>
              )}
              {!isCardOnly(card) && !!card.research_plan?.length && (
                <ReadingDetails summary="补证问题与未解事项（旧记录）">
                  {() =>
                    card.research_plan!.map((item) => (
                      <section key={item.id}>
                        <h3>{item.question}</h3>
                        <p>{item.evaluation}</p>
                        {item.missing_evidence?.map((text, index) => (
                          <p key={index}>{text}</p>
                        ))}
                        {item.stop_reason && (
                          <p>本次查证范围：{item.stop_reason}</p>
                        )}
                        {refs(item.evidence_ids ?? [])}
                      </section>
                    ))
                  }
                </ReadingDetails>
              )}
            </>,
          )}
          {section(
            "notes",
            "我的备注",
            <>
              <p className="research-provenance">
                个人阅读记录，与原文内容、原作者观点和模型整理分开保存。
              </p>
              <CardNotes key={card.id} cardId={card.id} />
            </>,
          )}
        </div>
        {selectedEvidence && (
          <aside
            key={`${card.id}:${selectedEvidence.id}`}
            ref={evidencePanel}
            className="dossier-evidence-drawer"
            aria-label="引文核对"
          >
            <div className="dossier-evidence-toolbar">
              <h2>引文 {String(selectedIndex + 1).padStart(2, "0")}</h2>
              <Button
                variant="ghost"
                onClick={() => {
                  setSelected(undefined);
                  if (returnFocus.current?.isConnected) {
                    returnFocus.current.focus({ preventScroll: true });
                    returnFocus.current.scrollIntoView?.({ block: "nearest" });
                  } else {
                    document
                      .getElementById(
                        returnChapter.current || `${scope}-${activeChapter}`,
                      )
                      ?.scrollIntoView?.({ block: "start" });
                  }
                }}
              >
                关闭引文，返回阅读位置
              </Button>
            </div>
            <Evidence
              evidence={[selectedEvidence]}
              offset={selectedIndex}
              scope={scope}
              bookId={card.book_id}
              selected={selectedEvidence.id}
              historical={historical}
            />
            <EvidenceOriginal
              evidence={[selectedEvidence]}
              selected={selectedEvidence.id}
            />
          </aside>
        )}
      </div>
    </article>
  );
}
