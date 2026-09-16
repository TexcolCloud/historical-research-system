// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { respondWith } from "@/test/responses.ts";
import CardsPanel from "@/features/cards/CardsPanel.tsx";
import type { components } from "@/client/schema.d.ts";

type Card = components["schemas"]["CardDetail"];
type Item = components["schemas"]["DraftItem"];
const quote = "运送120吨。";
const text = `𠮷地原文：${quote}\n\n再次记载：${quote}\n\n注：此数仅计本月。`;
const start = Array.from(text.slice(0, text.lastIndexOf(quote))).length;
function item(values: Partial<Item>): Item {
  return {
    item_id: "e",
    kind: "evidence",
    title: "运输原表",
    section: "运输主题",
    text: "本月运输记录。",
    attribution: "原表编制者",
    epistemic_state: "stated",
    interpretation: "原表记作一百二十吨。",
    context: "仅统计本月运出量。",
    ...values,
  };
}
function fixture(id = "card-a"): Card {
  return {
    id,
    book_id: "book",
    run_id: "cards-run",
    title: id === "card-a" ? "运输范围研究" : "第二张卡片",
    state: "adopted",
    created_at: "2026-09-14T00:00:00Z",
    candidate: {
      title: "运输范围研究",
      document_type: "统计记录",
      source_layer: "原文与译注",
      tags: ["运输", "数量口径"],
      formation_date: {
        original: "1940年编制",
        start_year: 1940,
        end_year: 1940,
        precision: "year",
        state: "stated",
        basis: ["e"],
      },
      event_date: {
        original: "1939年末",
        start_year: 1939,
        end_year: 1939,
        precision: "month",
        state: "stated",
        basis: ["e"],
      },
      entities: [
        {
          kind: "place",
          original_name: "𠮷地",
          evidence_refs: ["e"],
          identity_basis: "原表题名",
        },
      ],
      items: [
        item({
          source_unit_ids: ["u"],
          selections: [{ unit_id: "u", quote, occurrence: 1 }],
          limitations: ["不得代表全年。"],
          alternatives: ["或存在其他运输渠道。"],
          questions: ["其他渠道尚需另查。"],
          table_reading: "表头口径为运出量，单位为吨。",
        }),
        item({
          item_id: "a",
          kind: "argument",
          title: "运输规模判断",
          text: "记录支持本月存在运输活动。",
          attribution: "研究者",
          epistemic_state: "inferred",
          evidence_refs: ["e"],
          interpretation: "",
          context: "",
        }),
      ],
      evidence_relations: [
        {
          argument_id: "a",
          evidence_id: "e",
          role: "limit",
          reason: "单月数据限制全年推断。",
          used_scope: "仅适用于本月与所列地区。",
        },
      ],
      no_argument_reason: null,
    },
    units: [
      {
        unit_id: "u",
        chapter_id: "chapter",
        run_id: "source-run",
        title: "运输章",
        text,
        pages: [3, 4, 5],
        start: 100,
        end: 100 + Array.from(text).length,
      },
    ],
    quote_locations: [
      {
        item_id: "e",
        selection_index: 0,
        unit_id: "u",
        start,
        end: start + Array.from(quote).length,
        pages: [4, 5],
        issue: null,
      },
    ],
    verdict: { human_approval: false, machine_approval: true },
  };
}
function History() {
  const location = useLocation(),
    navigate = useNavigate();
  return (
    <>
      <output aria-label="当前地址">{location.search}</output>
      <button onClick={() => navigate(-1)}>浏览器返回</button>
    </>
  );
}
function show(initial = "/books/book?tab=cards&card=card-a") {
  render(
    <MemoryRouter initialEntries={[initial]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <History />
        <CardsPanel bookId="book" published />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(
    () => {},
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function serve(cards: Card[], requests: string[] = []) {
  respondWith((request) => {
    expect(request.method).toBe("GET");
    const url = new URL(request.url);
    requests.push(url.pathname);
    return url.pathname === "/api/v2/cards"
      ? cards
      : cards.find((card) => url.pathname.endsWith(`/${card.id}`));
  });
}

test("shows attribution, inference, dates, entities and relationships with accessible evidence jumps", async () => {
  serve([fixture()]);
  show();
  await screen.findByRole("heading", { name: "运输范围研究" });
  expect(screen.getByText("原表编制者")).toBeTruthy();
  expect(screen.getByText("研究者")).toBeTruthy();
  expect(screen.getByText("研究推断")).toBeTruthy();
  expect(screen.getByText("1940年编制")).toBeTruthy();
  expect(screen.getByText("1939年末")).toBeTruthy();
  expect(screen.getByText("身份依据：原表题名")).toBeTruthy();
  for (const value of [
    "仅统计本月运出量。",
    "不得代表全年。",
    "或存在其他运输渠道。",
    "其他渠道尚需另查。",
    "表头口径为运出量，单位为吨。",
  ])
    expect(screen.getByText(value)).toBeTruthy();
  const evidence = screen.getByRole("region", { name: "运输原表" });
  await userEvent.click(
    within(screen.getByRole("region", { name: "形成时间" })).getByRole(
      "button",
      { name: "运输原表" },
    ),
  );
  expect(document.activeElement).toBe(evidence);
  const relation = within(evidence).getByRole("region", {
    name: "证据与论证关系",
  });
  expect(within(relation).getByText("限制")).toBeTruthy();
  expect(within(relation).getByText("单月数据限制全年推断。")).toBeTruthy();
  await userEvent.click(
    within(relation).getByRole("button", { name: "运输规模判断" }),
  );
  expect(document.activeElement).toBe(
    screen.getByRole("region", { name: "运输规模判断" }),
  );
  expect(screen.getByLabelText("当前地址").textContent).toBe(
    "?tab=cards&card=card-a",
  );
});

test("highlights the specified occurrence in Unicode source and links every actual quote page", async () => {
  serve([fixture()]);
  show();
  const button = await screen.findByRole("button", {
    name: "定位引文 1 · 查看出处",
  });
  await userEvent.click(button);
  const source = screen.getByRole("complementary", { name: "出处对照" });
  expect(document.activeElement).toBe(source);
  expect(source.querySelector("mark")?.textContent).toBe(quote);
  expect(source.querySelector("mark")?.previousSibling?.textContent).toContain(
    "再次记载：",
  );
  expect(
    within(source)
      .getByRole("link", { name: "对照原书 · 第 4 页" })
      .getAttribute("href"),
  ).toBe("/api/v2/runs/source-run/artifacts/original.pdf#page=4");
  expect(
    within(source).getByRole("link", { name: "对照原书 · 第 5 页" }),
  ).toBeTruthy();
  expect(
    within(source).queryByRole("link", { name: "对照原书 · 第 3 页" }),
  ).toBeNull();
  await userEvent.click(
    within(source).getByText("完整来源正文 · PDF 物理页 3、4、5"),
  );
  expect(within(source).getByText("注：此数仅计本月。")).toBeTruthy();
  await userEvent.click(
    within(source).getByRole("button", { name: "收起出处" }),
  );
  expect(document.activeElement).toBe(button);
});

test.each(["missing", "mismatch"])(
  "never guesses a page when quote location is %s",
  async (mode) => {
    const card = fixture();
    if (mode === "missing") card.quote_locations = [];
    else card.quote_locations![0].start = 0;
    serve([card]);
    show();
    await userEvent.click(
      await screen.findByRole("button", { name: "定位引文 1 · 查看出处" }),
    );
    const source = screen.getByRole("complementary");
    expect(within(source).getByRole("alert")).toBeTruthy();
    expect(source.querySelector("mark")).toBeNull();
    expect(within(source).queryByRole("link", { name: /对照原书/ })).toBeNull();
  },
);

test("switches and restores cached cards through URL history without stale source panels", async () => {
  const requests: string[] = [];
  serve([fixture(), fixture("card-b")], requests);
  show();
  await userEvent.click(
    await screen.findByRole("button", { name: "定位引文 1 · 查看出处" }),
  );
  await userEvent.click(
    within(screen.getByRole("navigation", { name: "本书史料卡" })).getByRole(
      "button",
      { name: /第二张卡片/ },
    ),
  );
  await screen.findByRole("heading", { name: "第二张卡片" });
  expect(screen.queryByRole("complementary")).toBeNull();
  expect(screen.getByLabelText("当前地址").textContent).toBe(
    "?tab=cards&card=card-b",
  );
  await userEvent.click(screen.getByRole("button", { name: "浏览器返回" }));
  await screen.findByRole("heading", { name: "运输范围研究" });
  expect(
    requests.filter((path) => path === "/api/v2/cards/card-a"),
  ).toHaveLength(1);
  expect(screen.queryByRole("complementary")).toBeNull();
});

test("rejects a foreign-card deep link without fetching another book's detail", async () => {
  const requests: string[] = [];
  serve([fixture()], requests);
  show("/books/book?tab=cards&card=foreign");
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain(
      "本书没有这张史料卡",
    ),
  );
  expect(requests).toEqual(["/api/v2/cards"]);
});

test("keeps the list usable and offers retry when one card detail fails", async () => {
  let failed = false;
  const card = fixture();
  respondWith((request) => {
    if (new URL(request.url).pathname === "/api/v2/cards") return [card];
    if (!failed) {
      failed = true;
      throw new Error("合成详情读取失败");
    }
    return card;
  });
  show();
  await screen.findByRole("alert");
  expect(screen.getByRole("navigation", { name: "本书史料卡" })).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "重新读取卡片" }));
  await screen.findByRole("heading", { name: "运输范围研究" });
});

test("does not present missing metadata or a pending candidate as established facts", async () => {
  const card = fixture();
  card.state = "needs_revision";
  card.candidate.entities = [];
  card.candidate.tags = [];
  card.candidate.formation_date = {
    original: "",
    start_year: null,
    end_year: null,
    state: "unknown",
    precision: "unknown",
    basis: [],
  };
  card.candidate.items[0].attribution = "";
  card.candidate.items[0].epistemic_state = "disputed";
  serve([card]);
  show();
  await screen.findByRole("heading", { name: "运输范围研究" });
  expect(screen.getByText("候选卡 · 尚未通过机器核验")).toBeTruthy();
  expect(screen.getByText("未明确，请结合原文判断")).toBeTruthy();
  expect(screen.getByText("存在分歧")).toBeTruthy();
  const date = screen.getByRole("region", { name: "形成时间" });
  expect(within(date).getByText("未明确")).toBeTruthy();
  expect(within(date).queryByRole("button")).toBeNull();
});
