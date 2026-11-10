// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import CardNotes from "./CardNotes";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("a late notes read cannot undo a successful save", async () => {
  let resolveLate!: (response: Response) => void;
  let reads = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PATCH")
        return Response.json({
          text: "刚保存的备注",
          version: 3,
          updated_at: 100,
        });
      if (++reads === 1)
        return Response.json({ text: "旧备注", version: 2, updated_at: 80 });
      return new Promise<Response>((resolve) => {
        resolveLate = resolve;
      });
    }),
  );
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={query}>
      <CardNotes cardId="card" />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByText("人工备注"));
  const notes = await screen.findByRole("textbox", { name: "人工备注内容" });
  await userEvent.clear(notes);
  await userEvent.type(notes, "刚保存的备注");
  act(() => {
    void query.refetchQueries({ queryKey: ["research", "notes", "card"] });
  });
  await vi.waitFor(() => expect(reads).toBe(2));
  await userEvent.click(screen.getByRole("button", { name: "保存备注" }));
  await screen.findByText(/人工备注已保存/);
  await act(async () => {
    resolveLate(Response.json({ text: "旧备注", version: 2, updated_at: 80 }));
  });
  expect((notes as HTMLTextAreaElement).value).toBe("刚保存的备注");
  expect(query.getQueryData(["research", "notes", "card"])).toMatchObject({
    text: "刚保存的备注",
    version: 3,
  });
});

test("notes conflict preserves human input and requires an explicit latest-version retry without starting card processing", async () => {
  const patches: unknown[] = [];
  const requests: string[] = [];
  let conflict = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(String(input));
      if (init?.method === "PATCH") {
        patches.push(JSON.parse(String(init.body)));
        if (!conflict) {
          conflict = true;
          return Response.json({ detail: "备注已有新版本" }, { status: 409 });
        }
        return Response.json({
          text: "我的独立阅读备注",
          version: 4,
          updated_at: 100,
        });
      }
      return Response.json({
        text: conflict ? "其他窗口已保存备注" : "旧人工备注",
        version: conflict ? 3 : 2,
        updated_at: 80,
      });
    }),
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <CardNotes cardId="card" />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByText("人工备注"));
  const notes = await screen.findByRole("textbox", { name: "人工备注内容" });
  await userEvent.clear(notes);
  await userEvent.type(notes, "我的独立阅读备注");
  await userEvent.click(screen.getByRole("button", { name: "保存备注" }));
  await screen.findByText(/备注已有新版本/);
  expect((notes as HTMLTextAreaElement).value).toBe("我的独立阅读备注");
  await userEvent.click(screen.getByRole("button", { name: "读取最新备注" }));
  await screen.findByText("其他窗口已保存备注");
  expect((notes as HTMLTextAreaElement).value).toBe("我的独立阅读备注");
  await userEvent.click(
    screen.getByRole("button", { name: "已核对最新备注，保留输入继续保存" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "保存备注" }));
  await screen.findByText(/人工备注已保存/);
  expect(patches).toEqual([
    { expected_version: 2, text: "我的独立阅读备注" },
    { expected_version: 3, text: "我的独立阅读备注" },
  ]);
  expect(
    requests.every((url) => url === "/api/v2/research/cards/card/notes"),
  ).toBe(true);
});

test("switching away retains an unsaved per-card draft and refresh warns until it is saved", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
      Response.json({
        text: init?.method === "PATCH" ? "未保存的研究记录" : "已保存备注",
        version: init?.method === "PATCH" ? 2 : 1,
        updated_at: 1,
      }),
    ),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = (id: string) => (
    <QueryClientProvider client={client}>
      <CardNotes key={id} cardId={id} />
    </QueryClientProvider>
  );
  const first = render(view("card-a"));
  await userEvent.click(screen.getByText("人工备注"));
  const field = await screen.findByRole("textbox", { name: "人工备注内容" });
  await userEvent.clear(field);
  await userEvent.type(field, "未保存的研究记录");
  const before = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(before);
  expect(before.defaultPrevented).toBe(true);
  first.unmount();
  const other = render(view("card-b"));
  await userEvent.click(screen.getByText("人工备注"));
  expect(
    (
      (await screen.findByRole("textbox", {
        name: "人工备注内容",
      })) as HTMLTextAreaElement
    ).value,
  ).toBe("已保存备注");
  other.unmount();
  render(view("card-a"));
  await userEvent.click(screen.getByText("人工备注"));
  expect(
    (
      (await screen.findByRole("textbox", {
        name: "人工备注内容",
      })) as HTMLTextAreaElement
    ).value,
  ).toBe("未保存的研究记录");
  await userEvent.click(screen.getByRole("button", { name: "保存备注" }));
  await screen.findByText(/人工备注已保存/);
  const saved = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(saved);
  expect(saved.defaultPrevented).toBe(false);
});
