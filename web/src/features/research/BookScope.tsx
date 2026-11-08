import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { readBooks } from "@/client/api";
import { booksKey } from "@/hooks/events";
import { Button } from "@/components/ui/button";

export default function BookScope({
  value,
  onApply,
}: {
  value: string[];
  onApply: (books: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState(value);
  const books = useQuery({
    queryKey: booksKey,
    queryFn: ({ signal }) => readBooks(signal),
    enabled: open,
    staleTime: 30000,
  });
  return (
    <div className="research-book-scope">
      <Button
        type="button"
        variant="outline"
        aria-expanded={open}
        onClick={() => {
          setSelected(value);
          setOpen(!open);
        }}
      >
        选择书籍
      </Button>
      {open && (
        <fieldset>
          <legend>联合查阅范围</legend>
          <p>不选择书籍时查阅全库；多选时只查这些书籍。</p>
          {books.isPending && <p role="status">正在读取书籍…</p>}
          {books.isError && (
            <p role="alert">
              {books.error.message}
              <Button type="button" onClick={() => void books.refetch()}>
                重新读取书籍
              </Button>
            </p>
          )}
          <div className="research-book-choices">
            {books.data
              ?.filter(
                (book) => book.state === "ready" || book.revision_pending,
              )
              .map((book) => (
                <label key={book.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(book.id)}
                    onChange={(event) =>
                      setSelected(
                        event.target.checked
                          ? [...selected, book.id]
                          : selected.filter((id) => id !== book.id),
                      )
                    }
                  />
                  <span>{book.title}</span>
                </label>
              ))}
          </div>
          <div className="research-decision-actions">
            <Button
              type="button"
              onClick={() => {
                onApply([...new Set(selected)]);
                setOpen(false);
              }}
            >
              应用书籍范围
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                onApply([]);
                setOpen(false);
              }}
            >
              查阅全库
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setOpen(false)}
            >
              取消
            </Button>
          </div>
        </fieldset>
      )}
    </div>
  );
}
