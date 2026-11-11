import { Navigate, useSearchParams } from "react-router-dom";
import EntityLibrary from "@/features/research/EntityLibrary";
import ResearchDetail from "@/features/research/ResearchDetail";
import QuestionWorkspace from "@/features/research/QuestionWorkspace";
import { lazy, Suspense } from "react";
const EntityWorkspace = lazy(
  () => import("@/features/research/EntityWorkspace"),
);

export default function ResearchPage() {
  const [params] = useSearchParams();
  if (params.get("view") === "network")
    return (
      <Suspense fallback={<p role="status">正在打开关系与时间线…</p>}>
        <EntityWorkspace />
      </Suspense>
    );
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
