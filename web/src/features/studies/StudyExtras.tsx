import { Link, Navigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { studies, studyKeys } from "./api";
import { bookWorkspaceHref } from "@/features/books/workspace";
import { useWorkOverview } from "./work-overview";
import { useWorkspaceId } from "@/app/runtime-scope";
import { readResumeWork } from "@/app/workspace-resume";
import "./study-pages.css";
export { GlobalSearchPage } from "./StudySearch";
export { SettingsPage } from "./StudySettings";

function TopicLinks({
  topic,
}: {
  topic: { id: string; title: string; description: string };
}) {
  return (
    <article className="study-overview-card">
      <h2>
        <Link to={`/studies?topic=${encodeURIComponent(topic.id)}`}>
          {topic.title}
        </Link>
      </h2>
      <p>{topic.description || "尚未填写研究说明"}</p>
      <Link to={`/studies?topic=${encodeURIComponent(topic.id)}`}>
        进入专题 →
      </Link>
    </article>
  );
}

export function DashboardPage() {
  const topics = useQuery({
    queryKey: studyKeys.topics(),
    queryFn: ({ signal }) => studies.topics(false, signal),
  });
  const { books, pending, processing, researchPending, truncated } =
    useWorkOverview();
  const workspace = useWorkspaceId();
  const resume = useQuery({
    queryKey: ["platform", "resume", workspace],
    queryFn: () => readResumeWork(workspace) ?? null,
    enabled: !!workspace,
    staleTime: Infinity,
  });
  const hasPending =
    pending.length > 0 || (researchPending.data?.total ?? 0) > 0;
  return (
    <section className="study-page">
      <header className="study-page-heading">
        <div>
          <p className="study-eyebrow">个人研究空间</p>
          <h1>继续工作</h1>
          <p>先处理需要你判断的内容，再回到正在研究的专题。</p>
        </div>
        <Link
          to={hasPending ? "/reviews" : "/library"}
          className="study-primary-link"
        >
          {hasPending ? "开始核对" : "打开史料集"}
        </Link>
      </header>
      {resume.data && (
        <section className="study-home-resume" aria-label="上次工作现场">
          <div>
            <span>上次停留</span>
            <strong>{resume.data.title}</strong>
            <p>恢复原来的专题、页码、选择和筛选条件。</p>
          </div>
          <Link className="study-primary-link" to={resume.data.href}>
            继续上次工作
          </Link>
        </section>
      )}
      <div className="study-home-work">
        <section className="study-home-queue" aria-label="待你核对">
          <header>
            <h2>待你核对</h2>
            <Link to="/reviews">全部待办</Link>
          </header>
          {books.isPending && <p role="status">正在读取正文与目录待办…</p>}
          {pending.slice(0, 4).map((book) => (
            <Link
              className="study-home-work-item"
              key={book.id}
              to={bookWorkspaceHref(book)}
            >
              <strong>{book.title}</strong>
              <span>
                {book.state === "failed"
                  ? "处理未完成 · 查看原因并恢复"
                  : book.pending_count
                    ? `${book.pending_count} 页正文待你判断`
                    : book.navigation_pending_count ||
                        book.navigation_missing_page_count
                      ? "目录位置需要确认"
                      : "需要人工核对"}
              </span>
            </Link>
          ))}
          {researchPending.data && researchPending.data.total > 0 && (
            <Link className="study-home-work-item" to="/reviews">
              <strong>史料卡与实体关系</strong>
              <span>
                {truncated ? "至少 " : ""}
                {researchPending.data.total} 项研究待办
              </span>
            </Link>
          )}
          {books.isSuccess && researchPending.isSuccess && !hasPending && (
            <p>当前没有需要你判断的资料，可以继续阅读和研究。</p>
          )}
          {researchPending.isError && (
            <p role="alert">
              研究待办暂未读取成功。
              <Button
                variant="ghost"
                onClick={() => void researchPending.refetch()}
              >
                重新读取研究待办
              </Button>
            </p>
          )}
        </section>
        <section className="study-home-queue" aria-label="后台处理中">
          <header>
            <h2>后台处理中</h2>
            <Link to="/library">书籍进展</Link>
          </header>
          {processing.slice(0, 3).map((book) => (
            <Link
              className="study-home-work-item"
              key={book.id}
              to={`/books/${encodeURIComponent(book.id)}?tab=progress`}
            >
              <strong>{book.title}</strong>
              <span>
                {book.state === "queued" ? "排队中" : "正文处理中"}
                {book.pending_count ? " · 人工待核不阻塞其他页面" : ""}
              </span>
            </Link>
          ))}
          {books.isSuccess && !processing.length && (
            <p>暂无正在处理正文的书籍。制卡和分析进度可在对应任务中查看。</p>
          )}
        </section>
      </div>
      {books.isError && (
        <p role="alert">
          {books.error.message}
          <Button onClick={() => void books.refetch()}>重新读取书籍</Button>
        </p>
      )}
      {topics.isError && (
        <p role="alert">
          {topics.error.message}
          <Button onClick={() => void topics.refetch()}>重新读取专题</Button>
        </p>
      )}
      <header className="study-home-section-heading">
        <h2>最近专题</h2>
        <Link to="/studies">管理或新建专题</Link>
      </header>
      {topics.isPending ? (
        <p role="status">正在读取最近专题…</p>
      ) : topics.data?.items?.length ? (
        <div className="study-overview-grid">
          {topics.data.items.slice(0, 6).map((topic) => (
            <TopicLinks key={topic.id} topic={topic} />
          ))}
        </div>
      ) : topics.isSuccess ? (
        <div className="study-empty">
          <h2>建立第一个研究专题</h2>
          <p>导入和审核后的材料可以加入专题，整理成论据并放到画布上。</p>
          <Link to="/studies">创建专题</Link>
        </div>
      ) : null}
      <div className="study-inline-actions study-home-library-links">
        <span>
          {books.data?.length ?? "—"} 本史料 · {topics.data?.total ?? "—"}{" "}
          个专题
        </span>
        <Link to="/search">查找原文</Link>
        <Link to="/research">查阅实体与已核验关系</Link>
        <Link to="/library">打开史料集</Link>
      </div>
    </section>
  );
}

export function CanvasLandingPage() {
  const [params] = useSearchParams();
  const topicId = params.get("topic");
  const canvasId = params.get("canvas");
  if (topicId) {
    const target = `/studies/${encodeURIComponent(topicId)}/canvas`;
    return (
      <Navigate
        replace
        to={
          canvasId ? `${target}?canvas=${encodeURIComponent(canvasId)}` : target
        }
      />
    );
  }
  return <Navigate replace to="/studies" />;
}

export function UpdatesPage() {
  return (
    <section className="study-page">
      <header className="study-page-heading">
        <div>
          <h1>更新日志</h1>
          <p>当前版本 0.2.1 · 单人本地史料研究工作台</p>
        </div>
      </header>
      <article className="study-overview-card">
        <h2>专题研究空间</h2>
        <p>
          新增专题选材、论据丛、个人观点/问题/札记、自由画布、专题时间线和受控
          AI 建议；保留来源版本与原文定位。
        </p>
      </article>
      <article className="study-overview-card">
        <h2>关系查阅与时间线</h2>
        <p>
          有据实体关系按需加载，支持独立原文依据、日期精度与事项性质，以及原文返回后保留视口。
        </p>
      </article>
      <article className="study-overview-card">
        <h2>史料处理与核验</h2>
        <p>
          导入后由原生 Codex
          子代理识别、独立原图审核、修正验证、组织文章；正文入库后自动制作与核验史料卡。
        </p>
      </article>
    </section>
  );
}
