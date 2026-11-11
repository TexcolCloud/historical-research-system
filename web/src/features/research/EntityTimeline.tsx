import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  research,
  type ResearchTimelineData,
  type ResearchTimelineItem,
} from "./api";
import EventNature, { EventParticipants } from "./EventNature";
import Evidence from "./ResearchEvidence";

export type EntityTimelineProps = {
  entityId: string;
  onEntity: (id: string) => void;
  onCard: (id: string) => void;
  data?: ResearchTimelineData;
  selectedEventId?: string;
  onSelectEvent?: (event: ResearchTimelineItem) => void;
  onViewInNetwork?: (event: ResearchTimelineItem) => void;
  loading?: boolean;
  error?: string;
  onRetry?: () => void;
};
function TimelineDate({
  date,
  formation = false,
}: {
  date: ResearchTimelineItem["date"] | null | undefined;
  formation?: boolean;
}) {
  return (
    <p className="research-timeline-date">
      {formation && <span>文献形成日期 · </span>}
      {date?.text || date?.normalized || "未定年"}
      {date?.certainty === "disputed"
        ? " · 日期有争议"
        : date?.certainty === "inferred"
          ? " · 日期据材料推定"
          : date?.certainty === "unknown" && date?.text
            ? " · 日期尚未核对"
            : ""}
    </p>
  );
}

export default function EntityTimeline({
  entityId,
  onEntity,
  onCard,
  data,
  selectedEventId,
  onSelectEvent,
  onViewInNetwork,
  loading,
  error,
  onRetry,
}: EntityTimelineProps) {
  const controlled = !!(onSelectEvent || onViewInNetwork || data);
  const timeline = useQuery({
    queryKey: ["research", "timeline", entityId],
    queryFn: ({ signal }) => research.timeline(entityId, signal),
    staleTime: 30000,
    retry: false,
    enabled: !controlled,
  });
  return (
    <section className="research-timeline" aria-label="相关事项时间线">
      <h3>相关事项时间线</h3>
      <p className="research-provenance">
        按材料中的日期排列，未定年的事项另列。拟议、条件与否定事项保留原性质。
      </p>
      {controlled && data?.date_basis === "formation" && (
        <p className="research-provenance">文献形成日期不代表事项发生日期。</p>
      )}
      {(controlled ? loading : timeline.isPending) && (
        <p role="status">正在读取相关事项…</p>
      )}
      {(controlled ? error : timeline.isError) && (
        <p role="alert">
          {error || timeline.error?.message}
          <Button
            onClick={() => (onRetry ? onRetry() : void timeline.refetch())}
          >
            重新读取事项
          </Button>
        </p>
      )}
      {(controlled
        ? data?.items.length === 0
        : timeline.data?.items.length === 0) && (
        <p>当前范围没有可用的事项记录。</p>
      )}
      {controlled ? (
        <>
          {!!data?.items.length && (
            <p className="research-timeline-page-summary">
              显示第 {data.offset + 1}–{data.offset + data.items.length} 项，共{" "}
              {data.total} 项。
            </p>
          )}
          {[false, true].map((undated) => {
            const items =
              data?.items.filter((item) => item.undated === undated) ?? [];
            if (!items.length) return null;
            return (
              <section
                key={String(undated)}
                className="research-timeline-group"
              >
                <h4>
                  {undated
                    ? "未定年"
                    : data?.date_basis === "formation"
                      ? "按文献形成日期"
                      : "按事项日期"}
                </h4>
                <ol className="research-timeline-list">
                  {items.map((item) => (
                    <li
                      key={item.id}
                      data-selected={selectedEventId === item.id}
                    >
                      <TimelineDate
                        date={item.date}
                        formation={item.date_basis === "formation"}
                      />
                      <button
                        className="research-inline-link research-timeline-item-title"
                        aria-pressed={selectedEventId === item.id}
                        onClick={() => onSelectEvent?.(item)}
                      >
                        {item.name}
                      </button>
                      {item.item_kind === "event" && (
                        <EventNature value={item} />
                      )}
                      <p>{item.description}</p>
                      <EventParticipants value={item} />
                      {onViewInNetwork && (
                        <Button
                          variant="ghost"
                          aria-label={`在关系网中查看 · ${item.name}`}
                          onClick={() => onViewInNetwork(item)}
                        >
                          在关系网中查看
                        </Button>
                      )}
                    </li>
                  ))}
                </ol>
              </section>
            );
          })}
          {data?.truncated && (
            <p className="research-provenance">
              尚有记录未显示，可翻页继续查看。
            </p>
          )}
        </>
      ) : (
        <ol className="research-timeline-list">
          {timeline.data?.items.map((event) => (
            <li key={event.id}>
              <TimelineDate date={event.date} />
              <button
                className="research-inline-link"
                onClick={() => onEntity(event.id)}
              >
                {event.name}
              </button>
              <EventNature value={event} />
              <p>{event.description}</p>
              <Button variant="ghost" onClick={() => onCard(event.card_id)}>
                查看事项出处卡
              </Button>
              <details>
                <summary>核对事项依据</summary>
                <Evidence evidence={event.evidence} bookId={event.book_id} />
              </details>
            </li>
          ))}
        </ol>
      )}
      {!controlled && timeline.data?.truncated && (
        <p className="research-provenance">
          当前显示 {timeline.data.items.length}{" "}
          项，尚有其他事项，可进入具体事件继续追查。
        </p>
      )}
    </section>
  );
}
