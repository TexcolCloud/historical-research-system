import { lazy, Suspense } from "react";
import {
  Navigate,
  Route,
  Routes,
  useParams,
  useSearchParams,
} from "react-router-dom";
import AppShell from "@/app/AppShell.tsx";
const StudyLibrary = lazy(() => import("@/features/studies/StudyLibrary"));
const BookPage = lazy(() => import("@/routes/BookPage.tsx"));
const ReviewsPage = lazy(() => import("@/routes/ReviewsPage.tsx"));
const ResearchPage = lazy(() => import("@/routes/ResearchPage.tsx"));
const TopicsPage = lazy(() =>
  import("@/features/studies/StudyPages").then((m) => ({
    default: m.TopicsPage,
  })),
);
const ArgumentsPage = lazy(() =>
  import("@/features/studies/StudyPages").then((m) => ({
    default: m.ArgumentsPage,
  })),
);
const TopicTimelinePage = lazy(() =>
  import("@/features/studies/StudyPages").then((m) => ({
    default: m.TopicTimelinePage,
  })),
);
const AnalysisPage = lazy(() =>
  import("@/features/studies/StudyPages").then((m) => ({
    default: m.AnalysisPage,
  })),
);
const StudyCanvas = lazy(() => import("@/features/studies/StudyCanvas"));
const DashboardPage = lazy(() =>
  import("@/features/studies/StudyExtras").then((m) => ({
    default: m.DashboardPage,
  })),
);
const GlobalSearchPage = lazy(() =>
  import("@/features/studies/StudyExtras").then((m) => ({
    default: m.GlobalSearchPage,
  })),
);
const CanvasLandingPage = lazy(() =>
  import("@/features/studies/StudyExtras").then((m) => ({
    default: m.CanvasLandingPage,
  })),
);
const SettingsPage = lazy(() =>
  import("@/features/studies/StudyExtras").then((m) => ({
    default: m.SettingsPage,
  })),
);
const UpdatesPage = lazy(() =>
  import("@/features/studies/StudyExtras").then((m) => ({
    default: m.UpdatesPage,
  })),
);

function CanvasRoute() {
  const { topicId = "" } = useParams();
  const [params] = useSearchParams();
  return (
    <StudyCanvas
      topicId={topicId}
      canvasId={params.get("canvas") ?? undefined}
    />
  );
}

/** Route composition only; application providers and feature state live elsewhere. */
export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/welcome" element={<Navigate to="/library" replace />} />
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route
          path="/dashboard"
          element={
            <Suspense fallback={<p role="status">正在读取研究空间…</p>}>
              <DashboardPage />
            </Suspense>
          }
        />
        <Route
          path="/search"
          element={
            <Suspense fallback={<p role="status">正在打开全文检索…</p>}>
              <GlobalSearchPage />
            </Suspense>
          }
        />
        <Route
          path="/studies"
          element={
            <Suspense fallback={<p role="status">正在读取研究专题…</p>}>
              <TopicsPage />
            </Suspense>
          }
        />
        <Route
          path="/arguments"
          element={
            <Suspense fallback={<p role="status">正在读取研究记录…</p>}>
              <ArgumentsPage />
            </Suspense>
          }
        />
        <Route
          path="/timeline"
          element={
            <Suspense fallback={<p role="status">正在读取时间线索…</p>}>
              <TopicTimelinePage />
            </Suspense>
          }
        />
        <Route
          path="/analysis"
          element={
            <Suspense fallback={<p role="status">正在读取分析记录…</p>}>
              <AnalysisPage />
            </Suspense>
          }
        />
        <Route
          path="/workspace"
          element={
            <Suspense fallback={<p role="status">正在打开专题工作台…</p>}>
              <CanvasLandingPage />
            </Suspense>
          }
        />
        <Route
          path="/studies/:topicId/canvas"
          element={
            <Suspense fallback={<p role="status">正在打开专题画布…</p>}>
              <CanvasRoute />
            </Suspense>
          }
        />
        <Route
          path="/settings"
          element={
            <Suspense fallback={<p role="status">正在读取设置…</p>}>
              <SettingsPage />
            </Suspense>
          }
        />
        <Route
          path="/updates"
          element={
            <Suspense fallback={<p role="status">正在读取版本信息…</p>}>
              <UpdatesPage />
            </Suspense>
          }
        />
        <Route
          path="/research"
          element={
            <Suspense fallback={<p role="status">正在打开实体与卡片…</p>}>
              <ResearchPage />
            </Suspense>
          }
        />
        <Route
          path="/reviews"
          element={
            <Suspense fallback={<p role="status">正在打开待办…</p>}>
              <ReviewsPage />
            </Suspense>
          }
        />
        <Route
          path="/books/:bookId"
          element={
            <Suspense fallback={<p role="status">正在打开书籍…</p>}>
              <BookPage />
            </Suspense>
          }
        />
        <Route
          path="/library"
          element={
            <Suspense fallback={<p role="status">正在读取书籍…</p>}>
              <StudyLibrary />
            </Suspense>
          }
        />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  );
}
