import { lazy, Suspense } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { readBooks } from "@/client/api";
import { Button } from "@/components/ui/button";
const BooksPage = lazy(() => import("@/routes/BooksPage"));
const BookResearch = lazy(() => import("@/features/research/BookResearch"));

export default function StudyLibrary() {
  const [params, setParams] = useSearchParams();
  const cards = params.get("view") === "cards";
  const books = useQuery({
    queryKey: ["platform", "books"],
    queryFn: ({ signal }) => readBooks(signal),
    enabled: cards,
  });
  const requestedBook = params.get("book_id");
  const current = requestedBook
    ? books.data?.find((book) => book.id === requestedBook)
    : books.data?.[0];
  return (
    <section className="study-library">
      <nav className="study-library-tabs" aria-label="史料集内容">
        <Link to="/library" aria-current={!cards ? "page" : undefined}>
          书籍与原件
        </Link>
        <Link
          to="/library?view=cards"
          aria-current={cards ? "page" : undefined}
        >
          自动史料卡
        </Link>
        <Link to="/research">实体索引</Link>
      </nav>
      <Suspense fallback={<p role="status">正在打开史料集…</p>}>
        {!cards ? (
          <BooksPage />
        ) : (
          <>
            <header className="study-page-heading">
              <div>
                <h1>自动史料卡</h1>
                <p>查看已完成核验的文献、使用边界及规范出处。</p>
              </div>
              <label>
                来源书籍
                <select
                  value={current?.id ?? ""}
                  disabled={!books.data?.length}
                  onChange={(event) =>
                    setParams({ view: "cards", book_id: event.target.value })
                  }
                >
                  {!current && <option value="">请选择来源书籍</option>}
                  {books.data?.map((book) => (
                    <option value={book.id} key={book.id}>
                      {book.title}
                    </option>
                  ))}
                </select>
              </label>
            </header>
            {books.isError && (
              <p role="alert">
                {books.error.message}
                <Button onClick={() => void books.refetch()}>
                  重新读取书籍
                </Button>
              </p>
            )}
            {current ? (
              <BookResearch book={current} />
            ) : books.isPending ? (
              <p role="status">正在读取书籍…</p>
            ) : requestedBook && books.isSuccess ? (
              <p role="alert">所选书籍不在当前工作区，请重新选择来源书籍。</p>
            ) : books.isSuccess ? (
              <p>请先导入史料，入库后会自动制作史料卡。</p>
            ) : null}
          </>
        )}
      </Suspense>
    </section>
  );
}
