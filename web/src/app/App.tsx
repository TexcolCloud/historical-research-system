import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "@/app/AppShell.tsx";
const BooksPage = lazy(() => import("@/routes/BooksPage.tsx"));
const BookPage = lazy(() => import("@/routes/BookPage.tsx"));
const ReviewsPage = lazy(() => import("@/routes/ReviewsPage.tsx"));
const ResearchPage = lazy(() => import("@/routes/ResearchPage.tsx"));

/** Route composition only; application providers and feature state live elsewhere. */
export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/welcome" element={<Navigate to="/" replace />} />
        <Route
          path="/research"
          element={
            <Suspense fallback={<p role="status">正在打开研究检索…</p>}>
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
          path="*"
          element={
            <Suspense fallback={<p role="status">正在读取书籍…</p>}>
              <BooksPage />
            </Suspense>
          }
        />
      </Route>
    </Routes>
  );
}
