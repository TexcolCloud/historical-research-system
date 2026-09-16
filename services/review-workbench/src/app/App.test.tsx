// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { respondWith } from "@/test/responses";

const lifetime = vi.hoisted(() => ({ mounts: 0, stops: 0 }));
vi.mock("@/routes/BookPage.tsx", () => ({
  default: () => <h1>书籍详情</h1>,
}));
vi.mock("@/features/uploads/UploadPanel.tsx", async () => {
  const { useEffect } = await import("react");
  return {
    default: function Upload() {
      useEffect(() => {
        lifetime.mounts++;
        return () => {
          lifetime.stops++;
        };
      }, []);
      return <div data-testid="upload-session">synthetic upload</div>;
    },
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("navigation retains one workspace stream and an active upload until the application closes", async () => {
  const close = vi.fn();
  const streams: string[] = [];
  vi.stubGlobal(
    "EventSource",
    class {
      close = close;
      constructor(url: string) {
        streams.push(url);
      }
    },
  );
  respondWith(() => []);
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <MemoryRouter>
      <QueryClientProvider client={query}>
        <App />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "导入书籍" }));
  await screen.findByTestId("upload-session");
  await user.click(screen.getByRole("link", { name: "待办" }));
  await screen.findByRole("heading", { name: "待办" });
  expect(screen.getByTestId("upload-session")).toBeTruthy();
  expect(lifetime.mounts).toBe(1);
  expect(lifetime.stops).toBe(0);
  expect(streams).toEqual(["/api/v2/events"]);
  expect(close).not.toHaveBeenCalled();
  view.unmount();
  expect(lifetime.stops).toBe(1);
  expect(close).toHaveBeenCalledTimes(1);
  query.clear();
});

test("returning from book details retains the shelf search and status filter", async () => {
  vi.stubGlobal(
    "EventSource",
    class {
      close() {}
    },
  );
  respondWith(() => [
    {
      id: "review",
      title: "待审史料",
      state: "awaiting_review",
      created_at: "2026-09-01",
    },
    {
      id: "ready",
      title: "已入库史料",
      state: "ready",
      created_at: "2026-09-01",
    },
  ]);
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <MemoryRouter>
      <QueryClientProvider client={query}>
        <App />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.type(
    await screen.findByRole("textbox", { name: "搜索书籍" }),
    "史料",
  );
  await user.click(screen.getByRole("tab", { name: "需要核对" }));
  await user.click(await screen.findByRole("button", { name: /^待审史料/ }));
  await screen.findByRole("heading", { name: "书籍详情" });
  await user.click(screen.getByRole("link", { name: "书籍" }));
  expect(
    (
      (await screen.findByRole("textbox", {
        name: "搜索书籍",
      })) as HTMLInputElement
    ).value,
  ).toBe("史料");
  expect(
    screen.getByRole("tab", { name: "需要核对" }).getAttribute("aria-selected"),
  ).toBe("true");
  expect(await screen.findByRole("button", { name: /^待审史料/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^已入库史料/ })).toBeNull();
  view.unmount();
  query.clear();
});
