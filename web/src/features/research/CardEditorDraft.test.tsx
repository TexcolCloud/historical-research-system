// @vitest-environment happy-dom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import CardEditor from "./CardEditor";

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const initial = () =>
  JSON.parse(
    readFileSync(
      resolve(
        process.cwd(),
        "src/features/research/card-revision-contract.fixture.json",
      ),
      "utf8",
    ),
  ).initial;

function mount(
  workspace = "workspace-a",
  version = 1,
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  card = initial(),
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      Response.json(
        String(input).endsWith("/revisions")
          ? { run_id: "revision", state: "ready", card_only: true }
          : String(input).endsWith("/draft")
            ? {
                card,
                version,
                source_current: true,
                superseded: false,
                card_only: true,
              }
            : {
                ...card,
                version,
                book_id: "book",
                source_current: true,
                card_only: true,
              },
      ),
    ),
  );
  cache.setQueryData(["platform", "runtime"], { workspace_id: workspace });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={cache}>
        <CardEditor cardId="owner" onClose={() => {}} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

test.each([
  { evidence: [null] },
  { items: {} },
  { mentions: [null] },
  { dossier: {} },
])(
  "a JSON-valid but damaged cached draft never replaces the editable source card: %j",
  async (damage) => {
    localStorage.setItem(
      "hrs:card-revision:workspace-a:owner",
      JSON.stringify({
        schema: 1,
        workspaceId: "workspace-a",
        cardId: "owner",
        baseVersion: 1,
        draft: { ...initial(), summary: "损坏的缓存", ...damage },
      }),
    );
    mount();
    expect(await screen.findByDisplayValue(initial().summary)).toBeTruthy();
    expect(screen.queryByDisplayValue("损坏的缓存")).toBeNull();
  },
);

test("a complete v7 dossier cache retains its reading fields and exact unsaved content", async () => {
  const card = JSON.parse(
    readFileSync(
      resolve(
        process.cwd(),
        "src/features/research/dossier-revision-contract.fixture.json",
      ),
      "utf8",
    ),
  ).initial;
  localStorage.setItem(
    "hrs:card-revision:workspace-a:owner",
    JSON.stringify({
      schema: 1,
      workspaceId: "workspace-a",
      cardId: "owner",
      baseVersion: 1,
      draft: { ...card, summary: "尚未提交的完整v7卡片" },
    }),
  );
  mount("workspace-a", 1, undefined, card);
  expect(await screen.findByDisplayValue("尚未提交的完整v7卡片")).toBeTruthy();
  expect(screen.getByText(/已恢复本机草稿/)).toBeTruthy();
});

test("a complete revision draft survives remount in its original workspace without submitting it", async () => {
  const first = mount();
  const summary = await screen.findByRole("textbox", { name: "修订内容提要" });
  await userEvent.clear(summary);
  await userEvent.type(summary, "尚未提交的个人修订");
  first.unmount();
  mount();
  expect(await screen.findByDisplayValue("尚未提交的个人修订")).toBeTruthy();
  expect(screen.getByText(/已恢复本机草稿/)).toBeTruthy();
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([url]) => String(url).endsWith("/revisions")),
  ).toBe(false);
});

test("a restored older draft retains its original version until the user explicitly checks the current card", async () => {
  const first = mount();
  const summary = await screen.findByRole("textbox", { name: "修订内容提要" });
  await userEvent.clear(summary);
  await userEvent.type(summary, "旧版草稿仍由我核对");
  first.unmount();
  mount("workspace-a", 2);
  await screen.findByDisplayValue("旧版草稿仍由我核对");
  expect(
    screen
      .getByRole("button", { name: "提交修订核验" })
      .hasAttribute("disabled"),
  ).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "核对当前卡片" }));
  await userEvent.click(
    await screen.findByRole("button", {
      name: "已核对最新版本，保留草稿继续修订",
    }),
  );
  await userEvent.click(screen.getByRole("button", { name: "提交修订核验" }));
  await screen.findByText("修订已提交独立核验，原有卡片和人工决定继续保留。");
  const request = vi
    .mocked(fetch)
    .mock.calls.find(([url]) => String(url).endsWith("/revisions"))!;
  expect(JSON.parse(String(request[1]?.body)).expected_version).toBe(2);
  expect(localStorage.length).toBe(0);
});

test("a copied draft from another workspace is never restored under the current workspace key", async () => {
  const first = mount();
  const summary = await screen.findByRole("textbox", { name: "修订内容提要" });
  await userEvent.clear(summary);
  await userEvent.type(summary, "其他工作区的输入");
  first.unmount();
  const originalKey = localStorage.key(0)!;
  localStorage.setItem(
    originalKey.replace("workspace-a", "workspace-b"),
    localStorage.getItem(originalKey)!,
  );
  mount("workspace-b");
  expect(await screen.findByDisplayValue(initial().summary)).toBeTruthy();
  expect(screen.queryByDisplayValue("其他工作区的输入")).toBeNull();
});

test("unavailable browser storage retains the complete draft during in-app navigation without claiming durable storage", async () => {
  vi.stubGlobal("localStorage", {
    getItem: () => null,
    setItem: () => {
      throw new Error("quota");
    },
    removeItem: () => {},
    clear: () => {},
    length: 0,
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const first = mount("workspace-a", 1, cache);
  const summary = await screen.findByRole("textbox", { name: "修订内容提要" });
  await userEvent.clear(summary);
  await userEvent.type(summary, "只在当前会话保留的草稿");
  await screen.findByText(/本机存储暂不可用/);
  first.unmount();
  mount("workspace-a", 1, cache);
  expect(
    await screen.findByDisplayValue("只在当前会话保留的草稿"),
  ).toBeTruthy();
  expect(localStorage.length).toBe(0);
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(true);
});
