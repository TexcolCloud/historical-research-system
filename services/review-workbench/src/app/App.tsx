import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import AppShell from "@/app/AppShell.tsx";
const BooksPage = lazy(() => import("@/routes/BooksPage.tsx"));
const BookPage = lazy(() => import("@/routes/BookPage.tsx"));
const ReviewsPage = lazy(() => import("@/routes/ReviewsPage.tsx"));
const CardsPage = lazy(() => import("@/routes/CardsPage.tsx"));

/** Route composition only; application providers and feature state live elsewhere. */
export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route
          path="/reviews"
          element={
            <Suspense fallback={<p role="status">正在打开待办…</p>}>
              <ReviewsPage />
            </Suspense>
          }
        />
        <Route
          path="/cards"
          element={
            <Suspense fallback={<p role="status">正在打开史料卡…</p>}>
              <CardsPage />
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
