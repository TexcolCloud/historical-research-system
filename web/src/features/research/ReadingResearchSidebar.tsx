import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import type { ResearchReading } from "./api";
export default function ReadingResearchSidebar({
  data,
  returnTo,
  onClose,
  error,
  onRetry,
}: {
  data?: ResearchReading;
  returnTo: string;
  onClose: () => void;
  error?: Error | null;
  onRetry: () => void;
}) {
  return (
    <aside
      className="reader-search-sidebar reader-research-sidebar"
      aria-label="相关史料卡侧栏"
    >
      <header>
        <h2>相关史料卡</h2>
        <Button variant="ghost" onClick={onClose}>
          收起相关史料
        </Button>
      </header>
      {error ? (
        <p role="alert">
          {error.message}
          <Button onClick={onRetry}>重新读取相关史料</Button>
        </p>
      ) : !data ? (
        <p role="status">正在读取研究标注…</p>
      ) : (
        <>
          {!data.cards?.length && <p>这一篇尚无当前版本的已核验史料卡。</p>}
          {data.cards?.map((card) => (
            <article className="research-result" key={card.id}>
              <h3>
                <Link
                  to={`/research?${new URLSearchParams({ card: card.id, returnTo })}`}
                >
                  {card.title}
                </Link>
              </h3>
              <p>{card.summary}</p>
            </article>
          ))}
          {!!data.annotations?.length && (
            <>
              <h3>文中实体</h3>
              <div className="research-entity-chips">
                {[
                  ...new Map(
                    data.annotations.map((item) => [item.entity_id, item]),
                  ).values(),
                ].map((item) => (
                  <Link
                    key={item.entity_id}
                    to={`/research?${new URLSearchParams({ entity: item.entity_id, returnTo })}`}
                  >
                    {item.name}
                  </Link>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </aside>
  );
}
