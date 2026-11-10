// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import IdentityEditor from "./IdentityEditor";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("default identity proof selection and text inputs respect the actual HTTP request limits", async () => {
  const evidence = Array.from({ length: 101 }, (_, index) => ({
    id: `e${index}`,
    quote: `来源依据 ${index}`,
    pages: [1],
    footnote_ids: [],
  }));
  const entity = {
    id: "p",
    canonical_id: "p",
    name: "人物",
    kind: "person",
    version: 1,
    card_id: "card",
    book_id: "book",
    run_id: "run",
    state: "verified",
    source_current: true,
    evidence: [],
    source_mentions: [
      {
        id: "m",
        name: "人物",
        card_id: "card",
        card_version: 1,
        card_title: "原文",
        book_id: "book",
        evidence,
        evidence_refs: evidence.map((span) => ({
          card_id: "card",
          card_version: 1,
          evidence_id: span.id,
        })),
      },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(entity)),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <IdentityEditor entityId="p" onClose={() => {}} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "记录别名" }),
  );
  expect(
    screen
      .getAllByRole("checkbox")
      .filter((item) => (item as HTMLInputElement).checked),
  ).toHaveLength(100);
  expect(
    (screen.getByRole("textbox", { name: "来源别名" }) as HTMLInputElement)
      .maxLength,
  ).toBe(200);
  expect(
    (
      screen.getByRole("textbox", {
        name: "身份判断说明",
      }) as HTMLTextAreaElement
    ).maxLength,
  ).toBe(2000);
});

test("an incorrect current alias can be withdrawn with its source version and preserved history", async () => {
  const source = {
    id: "p",
    canonical_id: "p",
    version: 4,
    name: "张某",
    kind: "person",
    card_id: "c",
    book_id: "b",
    run_id: "r",
    state: "verified",
    source_current: true,
    evidence: [],
    aliases: [{ name: "张甲" }],
    source_mentions: [
      {
        id: "m",
        name: "张某",
        card_id: "c",
        card_version: 2,
        card_title: "落款",
        book_id: "b",
        evidence_refs: [{ card_id: "c", card_version: 2, evidence_id: "e" }],
        evidence: [{ id: "e", quote: "落款张某", pages: [1] }],
      },
    ],
  };
  let body: unknown;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/decisions")) {
        body = JSON.parse(String(init?.body));
        return Response.json({
          ...source,
          version: 5,
          aliases: [],
          withdrawn_aliases: [{ name: "张甲" }],
        });
      }
      return Response.json(source);
    }),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <IdentityEditor entityId="p" onClose={() => {}} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "移除别名" }),
  );
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "需要移除的别名" }),
    "张甲",
  );
  expect(
    (
      screen.getByRole("checkbox", {
        name: /本条目依据.*落款张某/,
      }) as HTMLInputElement
    ).checked,
  ).toBe(true);
  await userEvent.click(
    screen.getByRole("button", { name: "确认并保存身份决定" }),
  );
  await screen.findByText("身份决定已保存，原有来源和判断继续保留。");
  expect(body).toMatchObject({
    action: "alias_remove",
    alias: "张甲",
    expected_version: 4,
    evidence_refs: [{ card_id: "c", card_version: 2, evidence_id: "e" }],
  });
});
test("alias decisions cite a retained source version and keep human input when the identity changed", async () => {
  let body: unknown;
  const entity = {
    id: "person",
    canonical_id: "person",
    name: "张某",
    kind: "person",
    version: 2,
    card_id: "card",
    book_id: "book",
    run_id: "run",
    state: "verified",
    source_current: true,
    evidence: [],
    source_mentions: [
      {
        id: "m",
        name: "张某",
        card_id: "card",
        card_version: 3,
        card_title: "来信",
        book_id: "book",
        evidence_refs: [{ card_id: "card", card_version: 3, evidence_id: "e" }],
        evidence: [
          { id: "e", quote: "张某又名张甲", pages: [2], footnote_ids: [] },
        ],
      },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/decisions")) {
        body = JSON.parse(String(init?.body));
        return Response.json(
          { detail: "身份记录已更新，请核对最新版本。" },
          { status: 409 },
        );
      }
      return Response.json(entity);
    }),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <IdentityEditor entityId="person" onClose={() => {}} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "记录别名" }),
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "来源别名" }),
    "张甲",
  );
  expect(
    (
      screen.getByRole("checkbox", {
        name: /本条目依据.*张某又名张甲/,
      }) as HTMLInputElement
    ).checked,
  ).toBe(true);
  await userEvent.click(
    screen.getByRole("button", { name: "确认并保存身份决定" }),
  );
  await screen.findByText("身份记录已更新，请核对最新版本。");
  expect(body).toMatchObject({
    action: "alias",
    expected_version: 2,
    alias: "张甲",
    evidence_refs: [{ card_id: "card", card_version: 3, evidence_id: "e" }],
  });
  expect(
    (screen.getByRole("textbox", { name: "来源别名" }) as HTMLInputElement)
      .value,
  ).toBe("张甲");
});
