import { Link, useSearchParams } from "react-router-dom";
import ResearchSearch from "@/features/research/ResearchSearch";
import ResearchDetail from "@/features/research/ResearchDetail";
import QuestionWorkspace from "@/features/research/QuestionWorkspace";

export default function ResearchPage() {
  const [params] = useSearchParams();
  const detail = Boolean(params.get("card") || params.get("entity"));
  const questions = params.get("view") === "questions";
  return (
    <>
      <nav className="research-view-tabs" aria-label="研究工作区">
        <Link to="/research" aria-current={!questions ? "page" : undefined}>
          查阅资料
        </Link>
        <Link
          to="/research?view=questions"
          aria-current={questions ? "page" : undefined}
        >
          研究问题与选材
        </Link>
      </nav>
      {questions ? (
        <QuestionWorkspace />
      ) : (
        <div className="research-global-layout" data-detail={detail}>
          <ResearchSearch />
          <ResearchDetail />
        </div>
      )}
    </>
  );
}
