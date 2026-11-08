import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { research } from "./api";
import EventNature from "./EventNature";
import Evidence from "./ResearchEvidence";

export default function EntityTimeline({
  entityId,
  onEntity,
  onCard,
}: {
  entityId: string;
  onEntity: (id: string) => void;
  onCard: (id: string) => void;
}) {
  const timeline = useQuery({
    queryKey: ["research", "timeline", entityId],
    queryFn: ({ signal }) => research.timeline(entityId, signal),
    staleTime: 30000,
    retry: false,
  });
  return (
    <section className="research-timeline" aria-label="相关事项时间线">
      <h3>相关事项时间线</h3>
      <p className="research-provenance">
        按材料中的日期排列，未定年的事项另列。拟议、条件与否定事项保留原性质。
      </p>
      {timeline.isPending && <p role="status">正在读取相关事项…</p>}
      {timeline.isError && (
        <p role="alert">
          {timeline.error.message}
          <Button onClick={() => void timeline.refetch()}>重新读取事项</Button>
        </p>
      )}
      {timeline.data?.items.length === 0 && <p>当前没有可用的已核验事项。</p>}
      <ol>
        {timeline.data?.items.map((event) => (
          <li key={event.id}>
            <p>
              {event.date?.text || "未定年"}
              {event.date?.certainty === "disputed"
                ? " · 日期有争议"
                : event.date?.certainty === "inferred"
                  ? " · 日期据材料推定"
                  : ""}
            </p>
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
      {timeline.data?.truncated && (
        <p className="research-provenance">
          当前仅展示前 100 项，可进入具体事件继续追查。
        </p>
      )}
    </section>
  );
}
