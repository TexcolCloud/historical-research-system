// @vitest-environment happy-dom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { components } from "@/client/schema";
import CardEditor from "./CardEditor";

type Card = components["schemas"]["CardOnlyCardDraft"];
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function largeCard(): Card {
  const initial: Card = JSON.parse(
    readFileSync(
      resolve(
        process.cwd(),
        "src/features/research/card-revision-contract.fixture.json",
      ),
      "utf8",
    ),
  ).initial;
  const evidence = Array.from({ length: 76 }, (_, index) => ({
    ...initial.evidence[index % 2],
    id: `e${index}`,
  }));
  const mentions = Array.from({ length: 63 }, (_, index) => ({
    ...initial.mentions![0],
    id: `m${index}`,
    name: `人物${index}`,
    identity_key: `primary:m${index}`,
    evidence_ids: ["e1"],
  }));
  const events = Array.from({ length: 20 }, (_, index) => ({
    ...initial.events![0],
    id: `event${index}`,
    name: `事件${index}`,
    identity_key: `primary:event${index}`,
    evidence_ids: ["e1"],
    participant_ids: ["m0"],
    participant_roles: [
      {
        ...initial.events![0].participant_roles[0],
        mention_id: "m0",
        evidence_ids: ["e1"],
      },
    ],
  }));
  const relations = Array.from({ length: 24 }, (_, index) => ({
    ...initial.relations![0],
    id: `relation${index}`,
    subject_id: "m0",
    object_id: "event0",
    predicate: `关系${index}`,
    evidence_ids: ["e1"],
  }));
  return {
    ...initial,
    evidence,
    mentions,
    events,
    relations,
    coverage: initial.coverage.map((unit, index) => ({
      ...unit,
      mention_ids: index ? mentions.map((item) => item.id) : [],
      event_ids: index ? events.map((item) => item.id) : [],
      relation_ids: index ? relations.map((item) => item.id) : [],
    })),
  };
}

test("a source card with 63 entities, 76 quotes, 20 events and 24 relationships mounts only visible editing controls", async () => {
  const card = largeCard();
  let submitted: unknown;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/revisions")) {
        submitted = JSON.parse(String(init?.body));
        return Response.json({
          run_id: "revision",
          state: "ready",
          card_only: true,
        });
      }
      return Response.json(
        String(input).endsWith("/draft")
          ? {
              card,
              version: 1,
              source_current: true,
              superseded: false,
              card_only: true,
            }
          : {
              ...card,
              book_id: "book",
              version: 1,
              source_current: true,
              card_only: true,
            },
      );
    }),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <CardEditor cardId="owner" onClose={() => {}} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByRole("textbox", { name: "修订内容提要" });
  expect(
    document.querySelectorAll("input, textarea, select").length,
  ).toBeLessThanOrEqual(30);
  await userEvent.click(screen.getByText("实体记载"));
  await screen.findByText("实体 63 · 人物62");
  expect(
    document.querySelectorAll("input, textarea, select").length,
  ).toBeLessThanOrEqual(30);
  await userEvent.click(screen.getByText("实体 1 · 人物0"));
  const name = await screen.findByRole("textbox", { name: "名称" });
  expect(
    document.querySelectorAll("input, textarea, select").length,
  ).toBeLessThanOrEqual(200);
  await userEvent.clear(name);
  await userEvent.type(name, "已修改的实体称呼");
  await userEvent.click(screen.getByText("实体 1 · 已修改的实体称呼"));
  expect(screen.queryByRole("textbox", { name: "名称" })).toBeNull();
  await userEvent.click(screen.getByText("实体记载"));
  for (const [section, last] of [
    ["事件及参与者角色", "事件 20 · 事件19"],
    ["本卡关系与依据", "关系 24 · 关系23"],
    ["引文与原文范围", /^证据 76 ·/],
  ] as const) {
    await userEvent.click(screen.getByText(section));
    await screen.findByText(last);
    expect(
      document.querySelectorAll("input, textarea, select").length,
    ).toBeLessThanOrEqual(30);
    await userEvent.click(screen.getByText(section));
  }
  await userEvent.click(screen.getByText("实体记载"));
  await userEvent.click(await screen.findByText("实体 1 · 已修改的实体称呼"));
  expect(
    ((await screen.findByRole("textbox", { name: "名称" })) as HTMLInputElement)
      .value,
  ).toBe("已修改的实体称呼");
  await userEvent.click(screen.getByText("实体记载"));
  await userEvent.click(screen.getByRole("button", { name: "提交修订核验" }));
  await screen.findByText("修订已提交独立核验，原有卡片和人工决定继续保留。");
  expect(submitted).toEqual({
    expected_version: 1,
    card: {
      ...card,
      mentions: card.mentions!.map((item, index) =>
        index ? item : { ...item, name: "已修改的实体称呼" },
      ),
    },
  });
});

test("validation opens only the specific missing occurrence and offers repeatable focus without mounting other editors", async () => {
  const scrolled: string[] = [];
  vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(function (
    this: Element,
  ) {
    scrolled.push(this.querySelector("summary")?.textContent ?? "");
  });
  const card = largeCard();
  card.mentions![42].locations = [];
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      requests.push(String(input));
      return Response.json(
        String(input).endsWith("/draft")
          ? {
              card,
              version: 1,
              source_current: true,
              superseded: false,
              card_only: true,
            }
          : {
              ...card,
              book_id: "book",
              version: 1,
              source_current: true,
              card_only: true,
            },
      );
    }),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <CardEditor cardId="owner" onClose={() => {}} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByRole("textbox", { name: "修订内容提要" });
  await userEvent.click(screen.getByRole("button", { name: "提交修订核验" }));
  await screen.findByText("需要补充：实体：人物42");
  expect(
    ((await screen.findByRole("textbox", { name: "名称" })) as HTMLInputElement)
      .value,
  ).toBe("人物42");
  expect(scrolled).toContain("实体 43 · 人物42");
  expect(
    document.querySelectorAll("input, textarea, select").length,
  ).toBeLessThanOrEqual(200);
  expect(screen.queryByRole("textbox", { name: "事件名称" })).toBeNull();
  await userEvent.click(screen.getByText("实体 43 · 人物42"));
  expect(screen.queryByRole("textbox", { name: "名称" })).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "定位需要补充的位置" }),
  );
  expect(
    ((await screen.findByRole("textbox", { name: "名称" })) as HTMLInputElement)
      .value,
  ).toBe("人物42");
  expect(requests.some((url) => url.endsWith("/revisions"))).toBe(false);
});

test("switching card IDs resets the whole editor session rather than retaining the previous card's draft or sections", async () => {
  const cards = {
    first: { ...largeCard(), id: "first", summary: "第一张来源摘要" },
    second: { ...largeCard(), id: "second", summary: "第二张来源摘要" },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const card = String(input).includes("/cards/first")
        ? cards.first
        : cards.second;
      return Response.json(
        String(input).endsWith("/draft")
          ? {
              card,
              version: 1,
              source_current: true,
              superseded: false,
              card_only: true,
            }
          : {
              ...card,
              book_id: "book",
              version: 1,
              source_current: true,
              card_only: true,
            },
      );
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = (cardId: string) => (
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <CardEditor cardId={cardId} onClose={() => {}} />
      </QueryClientProvider>
    </MemoryRouter>
  );
  const rendered = render(view("first"));
  const summary = await screen.findByRole("textbox", { name: "修订内容提要" });
  await userEvent.clear(summary);
  await userEvent.type(summary, "尚未提交的第一张草稿");
  await userEvent.click(screen.getByText("实体记载"));
  await userEvent.click(await screen.findByText("实体 1 · 人物0"));
  await screen.findByRole("textbox", { name: "名称" });
  rendered.rerender(view("second"));
  await screen.findByDisplayValue("第二张来源摘要");
  expect(screen.queryByDisplayValue("尚未提交的第一张草稿")).toBeNull();
  expect(screen.queryByRole("textbox", { name: "名称" })).toBeNull();
  expect(
    document.querySelectorAll("input, textarea, select").length,
  ).toBeLessThanOrEqual(30);
});
