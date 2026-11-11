// @vitest-environment happy-dom
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCanvasDocument } from "./canvas-state";
import { studyKeys } from "./api";
import type { StudyCanvasDocument, StudyCanvasRecord } from "./types";

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
});

const initial: StudyCanvasRecord = {
  id: "c",
  topic_id: "t",
  title: "画布",
  version: 7,
  archived: false,
  created_at: 1,
  updated_at: 1,
  document: {
    viewport: { x: 0, y: 0, z: 1 },
    edges: [],
    nodes: [
      {
        id: "g",
        record_kind: "group",
        record_id: "",
        title: "未命名分组",
        x: 0,
        y: 0,
        width: 500,
        height: 300,
      },
    ],
  },
};
const renamed = (title: string): StudyCanvasDocument => ({
  ...initial.document,
  nodes: initial.document.nodes.map((node) => ({ ...node, title })),
});

function openDocument(cache: QueryClient, value = initial) {
  return renderHook(() => useCanvasDocument(value, "w"), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={cache}>{children}</QueryClientProvider>
    ),
  });
}

test("leaving before the debounce flushes an inline name and returning uses the acknowledged document", async () => {
  const cache = new QueryClient();
  const fetch = vi.fn(
    async (_url: RequestInfo | URL, options?: RequestInit) => {
      const body = JSON.parse(String(options?.body));
      return Response.json({ ...initial, document: body.document, version: 8 });
    },
  );
  vi.stubGlobal("fetch", fetch);
  const first = openDocument(cache);
  act(() => first.result.current.apply(renamed("阅读前的核对步骤")));
  first.unmount();
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  await waitFor(() =>
    expect(
      cache.getQueryData<StudyCanvasRecord>(studyKeys.canvas("c"))?.version,
    ).toBe(8),
  );
  const returned = openDocument(
    cache,
    cache.getQueryData<StudyCanvasRecord>(studyKeys.canvas("c"))!,
  );
  expect(returned.result.current.document.nodes[0].title).toBe(
    "阅读前的核对步骤",
  );
  expect(returned.result.current.dirty).toBe(false);
  expect(localStorage.getItem("hrs:study-canvas:w:c")).toBeNull();
});

test("returning and editing during a departure write queues the new draft without deleting it or reusing the old version", async () => {
  const cache = new QueryClient();
  const bodies: { expected_version: number; document: StudyCanvasDocument }[] =
    [];
  let acknowledgeFirst: (response: Response) => void = () => {};
  let acknowledgeSecond: (response: Response) => void = () => {};
  const firstResponse = new Promise<Response>((resolve) => {
    acknowledgeFirst = resolve;
  });
  const secondResponse = new Promise<Response>((resolve) => {
    acknowledgeSecond = resolve;
  });
  const fetch = vi.fn((_url: RequestInfo | URL, options?: RequestInit) => {
    bodies.push(JSON.parse(String(options?.body)));
    return bodies.length === 1 ? firstResponse : secondResponse;
  });
  vi.stubGlobal("fetch", fetch);
  const first = openDocument(cache);
  act(() => first.result.current.apply(renamed("离开时的名称")));
  act(() => {
    void first.result.current.save();
  });
  act(() => first.result.current.apply(renamed("旧编辑器最后的草稿")));
  first.unmount();
  const returned = openDocument(cache);
  act(() => returned.result.current.apply(renamed("返回后继续修改的名称")));
  let saved: Promise<boolean> = Promise.resolve(false);
  act(() => {
    saved = returned.result.current.save();
  });
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () =>
    acknowledgeFirst(
      Response.json({ ...initial, document: bodies[0].document, version: 8 }),
    ),
  );
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(bodies[1].expected_version).toBe(8);
  expect(
    JSON.parse(localStorage.getItem("hrs:study-canvas:w:c")!).document.nodes[0]
      .title,
  ).toBe("返回后继续修改的名称");
  await act(async () =>
    acknowledgeSecond(
      Response.json({ ...initial, document: bodies[1].document, version: 9 }),
    ),
  );
  expect(await saved).toBe(true);
  expect(returned.result.current.document.nodes[0].title).toBe(
    "返回后继续修改的名称",
  );
  expect(localStorage.getItem("hrs:study-canvas:w:c")).toBeNull();
});

test("leaving during a pending save flushes the edits made after its snapshot", async () => {
  const cache = new QueryClient();
  const bodies: { expected_version: number; document: StudyCanvasDocument }[] =
    [];
  let acknowledge: (response: Response) => void = () => {};
  const firstResponse = new Promise<Response>((resolve) => {
    acknowledge = resolve;
  });
  const fetch = vi.fn((_url: RequestInfo | URL, options?: RequestInit) => {
    bodies.push(JSON.parse(String(options?.body)));
    return bodies.length === 1
      ? firstResponse
      : Promise.resolve(
          Response.json({
            ...initial,
            document: bodies[1].document,
            version: 9,
          }),
        );
  });
  vi.stubGlobal("fetch", fetch);
  const editor = openDocument(cache);
  act(() => editor.result.current.apply(renamed("保存中的旧名称")));
  act(() => {
    void editor.result.current.save();
  });
  act(() => editor.result.current.apply(renamed("离开前最后修改的名称")));
  editor.unmount();
  await act(async () =>
    acknowledge(
      Response.json({ ...initial, document: bodies[0].document, version: 8 }),
    ),
  );
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(bodies[1].expected_version).toBe(8);
  expect(bodies[1].document.nodes[0].title).toBe("离开前最后修改的名称");
});

test("an unknown departure outcome retains the newest draft and does not issue a queued retry", async () => {
  const cache = new QueryClient();
  let fail: (reason: Error) => void = () => {};
  const response = new Promise<Response>((_resolve, reject) => {
    fail = reject;
  });
  const fetch = vi.fn(() => response);
  vi.stubGlobal("fetch", fetch);
  const first = openDocument(cache);
  act(() => first.result.current.apply(renamed("离开的草稿")));
  first.unmount();
  const returned = openDocument(cache);
  act(() => returned.result.current.apply(renamed("最新草稿必须保留")));
  let saved = Promise.resolve(true);
  act(() => {
    saved = returned.result.current.save();
  });
  await act(async () => fail(new TypeError("network response lost")));
  expect(await saved).toBe(false);
  expect(returned.result.current.error).toContain("尚未确认保存结果");
  expect(
    JSON.parse(localStorage.getItem("hrs:study-canvas:w:c")!).document.nodes[0]
      .title,
  ).toBe("最新草稿必须保留");
  returned.unmount();
  expect(fetch).toHaveBeenCalledTimes(1);
});

test("an acknowledgement does not rebase an unrelated older conflicting recovery draft", async () => {
  const cache = new QueryClient();
  let acknowledge: (response: Response) => void = () => {};
  const response = new Promise<Response>((resolve) => {
    acknowledge = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(() => response),
  );
  const editor = openDocument(cache);
  const current = renamed("当前编辑器的修改");
  act(() => editor.result.current.apply(current));
  editor.unmount();
  localStorage.setItem(
    "hrs:study-canvas:w:c",
    JSON.stringify({
      workspace_id: "w",
      topic_id: "t",
      canvas_id: "c",
      base_version: 6,
      document: renamed("已有冲突的旧草稿"),
    }),
  );
  await act(async () =>
    acknowledge(Response.json({ ...initial, document: current, version: 8 })),
  );
  expect(
    JSON.parse(localStorage.getItem("hrs:study-canvas:w:c")!).base_version,
  ).toBe(6);
});

test("undoing to the previously saved state during a pending write still flushes that latest command on departure", async () => {
  const cache = new QueryClient();
  const bodies: { expected_version: number; document: StudyCanvasDocument }[] =
    [];
  let acknowledge: (response: Response) => void = () => {};
  const firstResponse = new Promise<Response>((resolve) => {
    acknowledge = resolve;
  });
  const fetch = vi.fn((_url: RequestInfo | URL, options?: RequestInit) => {
    bodies.push(JSON.parse(String(options?.body)));
    return bodies.length === 1
      ? firstResponse
      : Promise.resolve(
          Response.json({
            ...initial,
            document: bodies[1].document,
            version: 9,
          }),
        );
  });
  vi.stubGlobal("fetch", fetch);
  const editor = openDocument(cache);
  act(() => editor.result.current.apply(renamed("正在发送的修改")));
  act(() => {
    void editor.result.current.save();
  });
  act(() => editor.result.current.undo());
  editor.unmount();
  await act(async () =>
    acknowledge(
      Response.json({ ...initial, document: bodies[0].document, version: 8 }),
    ),
  );
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(bodies[1]).toMatchObject({
    expected_version: 8,
    document: initial.document,
  });
});
