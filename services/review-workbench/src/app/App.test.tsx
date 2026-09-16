// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { respondWith } from "@/test/responses";

const lifetime = vi.hoisted(() => ({ mounts: 0, stops: 0 }));
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
