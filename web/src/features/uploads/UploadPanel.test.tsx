// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import UploadPanel from "./UploadPanel";

const session = vi.hoisted(() => ({
  files: [] as {
    progress: { uploadComplete: boolean; uploadStarted?: number };
    isPaused?: boolean;
    error?: string;
  }[],
  callbacks: new Map<string, Set<() => void>>(),
  destroy: vi.fn(),
}));
vi.mock("@uppy/react/dashboard", () => ({ default: () => null }));
vi.mock("./uploads", () => ({
  createBookUploader: () => ({
    uppy: {
      getFiles: () => session.files,
      on: (event: string, callback: () => void) => {
        const callbacks = session.callbacks.get(event) ?? new Set();
        callbacks.add(callback);
        session.callbacks.set(event, callbacks);
      },
      off: (event: string, callback: () => void) => {
        session.callbacks.get(event)?.delete(callback);
      },
      destroy: session.destroy,
    },
    cancelBooks: vi.fn(),
  }),
}));

afterEach(() => {
  cleanup();
  session.files = [];
  session.callbacks.clear();
  session.destroy.mockClear();
});

test("minimized uploads retain the session and report count changes without progress-render churn", () => {
  const activity = vi.fn();
  const query = new QueryClient();
  const view = (open: boolean) => (
    <QueryClientProvider client={query}>
      <UploadPanel
        open={open}
        onOpenChange={() => undefined}
        onActivityChange={activity}
      />
    </QueryClientProvider>
  );
  const mounted = render(view(true));
  expect(activity).toHaveBeenLastCalledWith({
    total: 0,
    pending: 0,
    uploading: 0,
  });
  act(() => {
    session.files = [
      { progress: { uploadComplete: false } },
      { progress: { uploadComplete: true } },
    ];
    session.callbacks.get("state-update")?.forEach((callback) => callback());
  });
  expect(activity).toHaveBeenLastCalledWith({
    total: 2,
    pending: 1,
    uploading: 0,
  });
  const count = activity.mock.calls.length;
  act(() =>
    session.callbacks.get("state-update")?.forEach((callback) => callback()),
  );
  expect(activity).toHaveBeenCalledTimes(count);
  mounted.rerender(view(false));
  expect(session.destroy).not.toHaveBeenCalled();
  mounted.unmount();
  expect(session.destroy).toHaveBeenCalledTimes(1);
  expect(session.callbacks.get("state-update")?.size).toBe(0);
  query.clear();
});

test("selected, paused and failed files are pending, but only a running transfer is uploading", () => {
  const activity = vi.fn();
  const query = new QueryClient();
  render(
    <QueryClientProvider client={query}>
      <UploadPanel
        open={true}
        onOpenChange={() => undefined}
        onActivityChange={activity}
      />
    </QueryClientProvider>,
  );
  const update = (file: (typeof session.files)[number]) =>
    act(() => {
      session.files = [file];
      session.callbacks.get("state-update")?.forEach((callback) => callback());
    });
  update({ progress: { uploadComplete: false } });
  expect(activity).toHaveBeenLastCalledWith({
    total: 1,
    pending: 1,
    uploading: 0,
  });
  update({ progress: { uploadComplete: false, uploadStarted: 1 } });
  expect(activity).toHaveBeenLastCalledWith({
    total: 1,
    pending: 1,
    uploading: 1,
  });
  update({
    progress: { uploadComplete: false, uploadStarted: 1 },
    isPaused: true,
  });
  expect(activity).toHaveBeenLastCalledWith({
    total: 1,
    pending: 1,
    uploading: 0,
  });
  update({
    progress: { uploadComplete: false, uploadStarted: 1 },
    error: "synthetic connection failure",
  });
  expect(activity).toHaveBeenLastCalledWith({
    total: 1,
    pending: 1,
    uploading: 0,
  });
  update({ progress: { uploadComplete: true, uploadStarted: 1 } });
  expect(activity).toHaveBeenLastCalledWith({
    total: 1,
    pending: 0,
    uploading: 0,
  });
  query.clear();
});
