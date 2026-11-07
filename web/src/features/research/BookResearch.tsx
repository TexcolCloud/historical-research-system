import { Link, useSearchParams } from "react-router-dom";
import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import type { Book } from "@/client/api";
import {
  research,
  researchKeys,
  ResearchError,
  type ResearchBook,
} from "./api";
import ResearchDetail, { cardStateLabel } from "./ResearchDetail";
import ResearchRunPanel from "./ResearchRun";

function PublishedBookResearch({ book }: { book: Book }) {
  const query = useQueryClient();
  const [params, setParams] = useSearchParams();
  const requestedOffset = Number(params.get("cardsOffset"));
  const offset =
    Number.isSafeInteger(requestedOffset) && requestedOffset > 0
      ? requestedOffset
      : 0;
  const detail = useQuery({
    queryKey: researchKeys.book(book.id),
    queryFn: ({ signal }) => research.book(book.id, signal),
    staleTime: 30000,
    refetchOnWindowFocus: false,
  });
  const cards = useQuery({
    queryKey: [...researchKeys.cards(book.id), offset],
    queryFn: ({ signal }) => research.cards(book.id, offset, signal),
    staleTime: 30000,
    refetchOnWindowFocus: false,
  });
  useEffect(() => {
    if (!cards.data || offset === 0 || offset < cards.data.total) return;
    const next = new URLSearchParams(params);
    next.set(
      "cardsOffset",
      String(Math.max(0, Math.floor((cards.data.total - 1) / 50) * 50)),
    );
    setParams(next, { replace: true });
  }, [cards.data?.total, offset, params, setParams]);
  const latestCurrent = detail.data?.runs.find(
    (run) => run.publication_sha256 === detail.data?.source_version,
  );
  const staleCount = detail.data?.stale_cards_count ?? 0;
  const outdatedRun =
    detail.data?.needs_update &&
    !detail.data.source_revision_pending &&
    staleCount > 0 &&
    latestCurrent?.state === "completed"
      ? latestCurrent
      : undefined;
  const active = outdatedRun ? undefined : latestCurrent;
  const start = useMutation({
    mutationFn: () => research.start(book.id, outdatedRun?.run_id),
    onSuccess: (run) => {
      query.setQueryData(researchKeys.run(run.run_id), run);
      query.setQueryData<ResearchBook>(
        researchKeys.book(book.id),
        (old) =>
          old && {
            ...old,
            runs: [
              run,
              ...old.runs.filter((item) => item.run_id !== run.run_id),
            ],
          },
      );
      void query.invalidateQueries({ queryKey: researchKeys.book(book.id) });
    },
  });
  const selected = params.get("card");
  const hasDetail = Boolean(selected || params.get("entity"));
  function page(nextOffset: number) {
    const next = new URLSearchParams(params);
    next.set("cardsOffset", String(nextOffset));
    setParams(next);
  }
  function openCard(id: string) {
    const next = new URLSearchParams(params);
    next.delete("entity");
    next.set("card", id);
    setParams(next);
  }
  if (detail.error instanceof ResearchError && detail.error.status === 404)
    return (
      <section className="workspace-empty">
        <h2>研究记录不可用</h2>
        <p>{detail.error.message}</p>
        <Link className="research-source-link" to="/">
          返回书籍
        </Link>
      </section>
    );
  return (
    <section className="research-book">
      <div className="research-book-tools">
        <Link
          className="research-source-link"
          to={`/research?${new URLSearchParams({ book_id: book.id })}`}
        >
          检索本书原文与史料卡
        </Link>
        <Button
          variant="ghost"
          disabled={detail.isFetching || cards.isFetching}
          onClick={() => {
            void detail.refetch();
            void cards.refetch();
          }}
        >
          刷新清单
        </Button>
      </div>
      {detail.isPending && <p role="status">正在读取研究任务…</p>}
      {detail.isError && (
        <p role="alert">
          {detail.error.message}
          <Button onClick={() => void detail.refetch()}>
            重新读取研究任务
          </Button>
        </p>
      )}
      {detail.data?.source_revision_pending && (
        <p className="workspace-status-note" role="note">
          原文修订核验中，当前研究仍基于此前已发布的版本。新原文发布后需更新核验。
        </p>
      )}
      {staleCount > 0 && (
        <div className="research-source-notice" role="note">
          <strong>
            {detail.data?.needs_update
              ? `${staleCount} 张历史卡片待更新`
              : `已保留 ${staleCount} 张旧版卡片`}
          </strong>
          <p>
            旧版记录和原始证据继续保留，当前清单使用当前原文对应的研究结果。
          </p>
        </div>
      )}
      {active ? (
        <ResearchRunPanel
          key={active.run_id}
          runId={active.run_id}
          initial={active}
          onOpenCard={openCard}
        />
      ) : (
        detail.data && (
          <div className="research-start">
            <div>
              <h2>
                {outdatedRun
                  ? "研究来源已更新，需重新核验"
                  : staleCount > 0
                    ? "当前原文尚无研究任务"
                    : "本书尚未制作史料卡"}
              </h2>
              <p>
                {detail.data.indexed
                  ? "基于已入库篇章制作卡片，并逐项核对原文证据。"
                  : "本书检索索引尚未就绪，完成入库后开放史料卡制作。"}
              </p>
            </div>
            <Button
              disabled={start.isPending || !detail.data.indexed}
              onClick={() => start.mutate()}
            >
              {staleCount > 0 ? "更新史料卡" : "开始制作史料卡"}
            </Button>
          </div>
        )
      )}
      {start.isPending && <p role="status">正在创建研究任务…</p>}
      {start.isError && <p role="alert">{start.error.message}</p>}
      {active &&
        [
          "ready",
          "running",
          "paused",
          "pending",
          "failed",
          "technical_blocked",
        ].includes(active.state) &&
        cards.data?.items.some((card) => card.run_id !== active.run_id) && (
          <p className="workspace-status-note" role="note">
            {["ready", "running"].includes(active.state)
              ? "新研究修订正在处理，清单保留此前完成的卡片。"
              : "新研究修订尚未完成，清单保留此前完成的卡片。"}
          </p>
        )}
      <div className="research-book-layout" data-detail={hasDetail}>
        <aside className="research-card-list" aria-label="本书史料卡">
          <header>
            <h2>史料卡</h2>
            <span>{cards.data?.total ?? detail.data?.cards_count ?? 0} 张</span>
          </header>
          {cards.isPending && <p role="status">正在读取卡片清单…</p>}
          {cards.isError && (
            <p role="alert">
              {cards.error.message}
              <Button onClick={() => void cards.refetch()}>
                重新读取卡片清单
              </Button>
            </p>
          )}
          {cards.data?.items.map((card) => (
            <button
              key={card.id}
              className="research-card-row"
              aria-pressed={selected === card.id}
              onClick={() => openCard(card.id)}
            >
              <span className="research-status" data-state={card.state}>
                {card.source_current === false || card.source_status === "stale"
                  ? "旧版卡片 · 待更新"
                  : cardStateLabel(card.state)}
              </span>
              <strong>{card.title}</strong>
              <span className="research-card-row-summary">{card.summary}</span>
              <small>{card.tags.join(" · ")}</small>
            </button>
          ))}
          {cards.data?.items.length === 0 && (
            <p className="research-empty">
              {staleCount > 0
                ? "当前版本尚无卡片。完成新研究后会更新此清单。"
                : "尚无卡片。任务生成的结果会保存在这里。"}
            </p>
          )}
          {cards.data && cards.data.total > 50 && (
            <nav className="research-pagination" aria-label="史料卡分页">
              <Button
                variant="outline"
                disabled={offset === 0 || cards.isFetching}
                onClick={() => page(Math.max(0, offset - 50))}
              >
                上一页
              </Button>
              <span>
                {offset + 1}–{Math.min(offset + 50, cards.data.total)} /{" "}
                {cards.data.total}
              </span>
              <Button
                variant="outline"
                disabled={offset + 50 >= cards.data.total || cards.isFetching}
                onClick={() => page(offset + 50)}
              >
                下一页
              </Button>
            </nav>
          )}
        </aside>
        {hasDetail ? (
          <ResearchDetail />
        ) : (
          <div className="research-selection-empty">
            <h2>选择史料卡</h2>
            <p>查看原文证据、模型提要、使用边界及人物事件关系。</p>
          </div>
        )}
      </div>
    </section>
  );
}

export default function BookResearch({ book }: { book: Book }) {
  if (book.state !== "ready" && !book.revision_pending)
    return (
      <section className="workspace-empty">
        <h2>本书尚未完成入库</h2>
        <p>完成正文核对、篇章整理和入库后，即可基于正式资料制作史料卡。</p>
        <Link
          className="research-source-link"
          to={`/books/${encodeURIComponent(book.id)}?tab=reader`}
        >
          阅读已有底稿
        </Link>
        {(book.pending_count ?? 0) > 0 && (
          <Link to={`/books/${encodeURIComponent(book.id)}?tab=review`}>
            处理待核对正文
          </Link>
        )}
      </section>
    );
  return <PublishedBookResearch key={book.id} book={book} />;
}
