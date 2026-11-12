import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { readBooks } from "@/client/api";
import { Button } from "@/components/ui/button";
import { studies, studyKeys } from "./api";
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
        <Link to={`/studies?topic=${topic.id}`}>{topic.title}</Link>
      </h2>
      <p>{topic.description || "尚未填写研究说明"}</p>
      <div className="study-inline-actions">
        <Link to={`/studies/${topic.id}/canvas`}>打开画布</Link>
        <Link to={`/timeline?topic=${topic.id}`}>时间线索</Link>
        <Link to={`/arguments?topic=${topic.id}`}>论据丛</Link>
      </div>
    </article>
  );
}

export function DashboardPage() {
  const topics = useQuery({
    queryKey: studyKeys.topics(),
    queryFn: ({ signal }) => studies.topics(false, signal),
  });
  const books = useQuery({
    queryKey: ["platform", "books"],
    queryFn: ({ signal }) => readBooks(signal),
  });
  return (
    <section className="study-page">
      <header className="study-page-heading">
        <div>
          <p className="study-eyebrow">个人研究空间</p>
          <h1>仪表盘</h1>
          <p>继续整理材料，或从一个新的专题开始。</p>
        </div>
        <Link to="/studies" className="study-primary-link">
          新建研究专题
        </Link>
      </header>
      <div className="study-overview-counts">
        <Link to="/library">
          <strong>{books.data?.length ?? "—"}</strong>本史料
        </Link>
        <Link to="/studies">
          <strong>{topics.data?.total ?? "—"}</strong>个研究专题
        </Link>
        <Link to="/reviews">查看处理待办</Link>
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
      <div className="study-inline-actions">
        <Link to="/search">查找原文</Link>
        <Link to="/research">查阅实体与已核验关系</Link>
        <Link to="/library">打开史料集</Link>
      </div>
    </section>
  );
}

export function CanvasLandingPage() {
  const topics = useQuery({
    queryKey: studyKeys.topics(),
    queryFn: ({ signal }) => studies.topics(false, signal),
  });
  return (
    <section className="study-page">
      <header className="study-page-heading">
        <div>
          <h1>专题工作台</h1>
          <p>选择专题进入可保存的研究画布。</p>
        </div>
        <Link to="/studies">管理专题</Link>
      </header>
      {topics.isPending && <p role="status">正在读取专题…</p>}
      {topics.isError && (
        <p role="alert">
          {topics.error.message}
          <Button onClick={() => void topics.refetch()}>重新读取专题</Button>
        </p>
      )}
      <div className="study-overview-grid">
        {topics.data?.items.map((topic) => (
          <TopicLinks key={topic.id} topic={topic} />
        ))}
      </div>
      {topics.data && topics.data.total > topics.data.items.length && (
        <p>
          当前显示最近 {topics.data.items.length} 个专题。
          <Link to="/studies">查看全部 {topics.data.total} 个专题</Link>
        </p>
      )}
      {topics.data?.total === 0 && (
        <p>
          还没有专题。<Link to="/studies">先创建一个专题</Link>，再放入材料。
        </p>
      )}
    </section>
  );
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
