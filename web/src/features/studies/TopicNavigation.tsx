import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useWorkspaceId } from "@/app/runtime-scope";
import { rememberResumeWork } from "@/app/workspace-resume";
import "./topic-navigation.css";

export type StudyView =
  "materials" | "records" | "canvas" | "timeline" | "analysis";

const views: [StudyView, string][] = [
  ["materials", "材料"],
  ["records", "研究记录"],
  ["canvas", "关系画布"],
  ["timeline", "时间线索"],
  ["analysis", "AI 分析"],
];

function topicViewHref(view: StudyView, topicId: string) {
  const id = encodeURIComponent(topicId);
  return view === "canvas"
    ? `/studies/${id}/canvas`
    : `${view === "materials" ? "/studies" : view === "records" ? "/arguments" : `/${view}`}?topic=${id}`;
}

function currentView(pathname: string): StudyView {
  if (/^\/studies\/[^/]+\/canvas$/.test(pathname)) return "canvas";
  if (pathname === "/arguments") return "records";
  if (pathname === "/timeline") return "timeline";
  if (pathname === "/analysis") return "analysis";
  return "materials";
}

function viewKey(workspace: string, topic: string, view: StudyView) {
  return `hrs:study-view:${encodeURIComponent(workspace)}:${encodeURIComponent(topic)}:${view}`;
}

function belongsToView(href: string, topic: string, view: StudyView) {
  if (!href.startsWith("/") || href.startsWith("//") || href.includes("\\"))
    return false;
  try {
    const url = new URL(href, "http://hrs.local");
    return (
      url.origin === "http://hrs.local" &&
      url.pathname === topicViewHref(view, topic).split("?")[0] &&
      (view === "canvas" || url.searchParams.get("topic") === topic)
    );
  } catch {
    return false;
  }
}

function rememberedView(workspace: string, topic: string, view: StudyView) {
  if (!workspace) return undefined;
  try {
    const href = sessionStorage.getItem(viewKey(workspace, topic, view));
    return href && belongsToView(href, topic, view) ? href : undefined;
  } catch {
    return undefined;
  }
}

/** Explicit topic links never broaden a personal research scope to the library. */
export function TopicNavigation({
  topicId,
  topicTitle,
  active,
}: {
  topicId: string;
  topicTitle?: string;
  active?: StudyView;
}) {
  const location = useLocation();
  const selected = active ?? currentView(location.pathname);
  const workspace = useWorkspaceId();
  const cache = useQueryClient();
  const currentHref = location.pathname + location.search;
  useEffect(() => {
    if (
      !workspace ||
      !topicId ||
      !belongsToView(currentHref, topicId, selected)
    )
      return;
    try {
      sessionStorage.setItem(
        viewKey(workspace, topicId, selected),
        currentHref,
      );
    } catch {
      // View restoration is optional; explicit links remain available without storage.
    }
    const resume = rememberResumeWork(
      workspace,
      currentHref,
      `专题：${topicTitle || "研究专题"}`,
    );
    if (resume) cache.setQueryData(["platform", "resume", workspace], resume);
  }, [workspace, topicId, selected, currentHref, topicTitle, cache]);
  if (!topicId) return null;
  return (
    <div className="study-workspace-navigation">
      <div className="study-topic-context" role="region" aria-label="当前专题">
        <span>当前专题</span>
        <strong>{topicTitle || "研究专题"}</strong>
        <Link to="/studies">切换或管理专题</Link>
      </div>
      <nav className="study-topic-navigation" aria-label="专题内导航">
        {views.map(([view, label]) => (
          <Link
            key={view}
            to={
              view === selected && belongsToView(currentHref, topicId, view)
                ? currentHref
                : (rememberedView(workspace, topicId, view) ??
                  topicViewHref(view, topicId))
            }
            aria-current={view === selected ? "page" : undefined}
          >
            {label}
          </Link>
        ))}
      </nav>
    </div>
  );
}
