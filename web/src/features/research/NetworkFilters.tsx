import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { readBooks } from "@/client/api";
import { booksKey } from "@/hooks/events";
import type { ResearchNetworkScope } from "./api";
import { entityLabels } from "./labels";
import { crossRelationLabels, relationLabel } from "./relation-labels";

export default function NetworkFilters({
  scope,
  predicates,
  onChange,
}: {
  scope: ResearchNetworkScope;
  predicates: string[];
  onChange: (changes: Record<string, string>) => void;
}) {
  const books = useQuery({
    queryKey: booksKey,
    queryFn: ({ signal }) => readBooks(signal),
    staleTime: 30_000,
    retry: false,
  });
  const relations = [
    ...new Set([
      ...Object.keys(crossRelationLabels),
      "participant",
      "mentioned_in",
      ...predicates,
      scope.predicate,
    ]),
  ].filter(Boolean);
  return (
    <div className="network-filters">
      <label>
        资料范围
        <select
          aria-label="资料范围"
          value={scope.bookId}
          onChange={(event) => onChange({ book_id: event.target.value })}
        >
          <option value="">全部已核验资料</option>
          {scope.bookId &&
            !books.data?.some((book) => book.id === scope.bookId) && (
              <option value={scope.bookId}>当前书籍</option>
            )}
          {(books.data ?? [])
            .filter(
              (book) =>
                !["deleted", "deleting", "delete_failed"].includes(book.state),
            )
            .map((book) => (
              <option key={book.id} value={book.id}>
                {book.title}
              </option>
            ))}
        </select>
      </label>
      <details>
        <summary>时间与关系筛选</summary>
        <form
          key={JSON.stringify(scope)}
          onSubmit={(event) => {
            event.preventDefault();
            const fields = new FormData(event.currentTarget);
            onChange({
              date_from: String(fields.get("from") || "").trim(),
              date_to: String(fields.get("to") || "").trim(),
              predicate: String(fields.get("predicate") || "").trim(),
              node_kind: String(fields.get("kind") || ""),
              undated: fields.has("undated") ? "" : "false",
              sources: fields.has("sources") ? "true" : "",
            });
          }}
        >
          <label>
            起始时间
            <input
              name="from"
              aria-label="起始时间"
              placeholder="1948 或 1948-01-09"
              defaultValue={scope.dateFrom}
              maxLength={10}
              pattern="[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?"
            />
          </label>
          <label>
            结束时间
            <input
              name="to"
              aria-label="结束时间"
              placeholder="1949 或 1949-12"
              defaultValue={scope.dateTo}
              maxLength={10}
              pattern="[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?"
            />
          </label>
          <p>支持年、月或日；保留原文精度。关系图按关系记载时间筛选。</p>
          <label>
            图中实体类型
            <select
              name="kind"
              aria-label="图中实体类型"
              defaultValue={scope.nodeKind}
            >
              <option value="">全部类型</option>
              {Object.entries(entityLabels).map(([kind, label]) => (
                <option value={kind} key={kind}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            图中关系名称
            <select
              name="predicate"
              aria-label="图中关系名称"
              defaultValue={scope.predicate}
            >
              <option value="">全部关系</option>
              {relations.map((predicate) => (
                <option value={predicate} key={predicate}>
                  {relationLabel(predicate)}
                </option>
              ))}
            </select>
          </label>
          <label className="network-checkbox">
            <input
              type="checkbox"
              name="undated"
              defaultChecked={scope.includeUndated}
            />
            保留未定年记录
          </label>
          <label className="network-checkbox">
            <input
              type="checkbox"
              name="sources"
              defaultChecked={scope.includeSources}
            />
            图中展开出处卡片
          </label>
          <div>
            <Button type="submit">应用筛选</Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() =>
                onChange({
                  date_from: "",
                  date_to: "",
                  predicate: "",
                  node_kind: "",
                  undated: "",
                  sources: "",
                })
              }
            >
              清除筛选
            </Button>
          </div>
        </form>
      </details>
      {books.isError && (
        <p role="alert">
          书籍选项暂不可用，当前范围仍保留。
          <button onClick={() => void books.refetch()}>重新读取书籍</button>
        </p>
      )}
    </div>
  );
}
