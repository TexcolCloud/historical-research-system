import { useSearchParams } from "react-router-dom";
import ResearchSearch from "@/features/research/ResearchSearch";
import ResearchDetail from "@/features/research/ResearchDetail";

export default function ResearchPage() {
  const [params] = useSearchParams();
  const detail = Boolean(params.get("card") || params.get("entity"));
  return (
    <div className="research-global-layout" data-detail={detail}>
      <ResearchSearch />
      <ResearchDetail />
    </div>
  );
}
