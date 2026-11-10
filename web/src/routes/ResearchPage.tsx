import { Navigate, useSearchParams } from "react-router-dom";
import EntityLibrary from "@/features/research/EntityLibrary";
import ResearchDetail from "@/features/research/ResearchDetail";
import QuestionWorkspace from "@/features/research/QuestionWorkspace";

export default function ResearchPage() {
  const [params] = useSearchParams();
  const detail = Boolean(params.get("card") || params.get("entity"));
  const questions = params.get("view") === "questions";
  const oldBook = params.get("book_id");
  if (!questions && !detail && oldBook)
    return (
      <Navigate
        replace
        to={`/books/${encodeURIComponent(oldBook)}?${new URLSearchParams({ tab: "reader", section: "search", q: params.get("q") ?? "" })}`}
      />
    );
  return (
    <>
      {questions ? (
        <QuestionWorkspace />
      ) : (
        <div className="research-global-layout" data-detail={detail}>
          <EntityLibrary />
          <ResearchDetail />
        </div>
      )}
    </>
  );
}
