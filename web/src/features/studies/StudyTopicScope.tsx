import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { studies, studyKeys } from "./api";

export function StudyErrorState({
  error,
  retry,
}: {
  error: Error | null;
  retry: () => void;
}) {
  return (
    <div role="alert" className="study-feedback">
      <p>{error?.message || "暂时无法读取研究记录。"}</p>
      <Button variant="outline" onClick={retry}>
        重新读取
      </Button>
    </div>
  );
}

/** The selected topic is visible in the URL; global library search has its own scope. */
export function useStudyTopic() {
  const [params, setParams] = useSearchParams();
  const topicId = params.get("topic") || "";
  const topics = useQuery({
    queryKey: studyKeys.topics(),
    queryFn: ({ signal }) => studies.topics(false, signal),
    staleTime: 30_000,
    retry: false,
  });
  const topic = useQuery({
    queryKey: studyKeys.topic(topicId),
    queryFn: ({ signal }) => studies.topic(topicId, signal),
    enabled: !!topicId,
    staleTime: 30_000,
    retry: false,
  });
  function select(id: string) {
    const next = new URLSearchParams(params);
    if (id) next.set("topic", id);
    else next.delete("topic");
    next.delete("offset");
    next.delete("analysis");
    next.delete("selected");
    next.delete("material_id");
    setParams(next);
  }
  return { topicId, topics, topic, select };
}

export function TopicChooser({
  scope,
}: {
  scope: ReturnType<typeof useStudyTopic>;
}) {
  if (scope.topics.isError)
    return (
      <StudyErrorState
        error={scope.topics.error}
        retry={() => void scope.topics.refetch()}
      />
    );
  return (
    <div className="study-topic-chooser">
      <label>
        研究专题
        <select
          aria-label="研究专题"
          value={scope.topicId}
          onChange={(event) => scope.select(event.target.value)}
          disabled={scope.topics.isPending}
        >
          <option value="">
            {scope.topics.isPending ? "正在读取专题…" : "选择研究专题"}
          </option>
          {scope.topics.data?.items.map((topic) => (
            <option key={topic.id} value={topic.id}>
              {topic.title}
            </option>
          ))}
          {scope.topic.data &&
            !scope.topics.data?.items.some(
              (topic) => topic.id === scope.topicId,
            ) && (
              <option value={scope.topicId}>{scope.topic.data.title}</option>
            )}
        </select>
      </label>
      {scope.topics.data &&
        scope.topics.data.total > scope.topics.data.items.length && (
          <Link to="/studies">查看全部 {scope.topics.data.total} 个专题</Link>
        )}
    </div>
  );
}

export function ChooseTopic({
  message = "选择一个专题，继续整理当前材料。",
}: {
  message?: string;
}) {
  return (
    <div className="study-empty">
      <h2>从研究专题开始</h2>
      <p>{message}</p>
      <Link to="/studies">创建或管理专题</Link>
    </div>
  );
}
