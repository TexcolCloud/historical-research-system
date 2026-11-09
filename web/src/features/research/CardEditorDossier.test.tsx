// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Draft } from "./CardFields";
import type { components } from "@/client/schema";
import CardEditor from "./CardEditor";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function contract() {
  return JSON.parse(
    readFileSync(
      resolve(
        process.cwd(),
        "src/features/research/dossier-revision-contract.fixture.json",
      ),
      "utf8",
    ),
  ) as {
    initial: components["schemas"]["DossierCardDraft"];
    edited_dossier: components["schemas"]["CardDossier"];
    new_unit: components["schemas"]["DossierArgumentUnit"];
  };
}

function mountEditor(initial: Draft, alternatives: Record<string, Draft> = {}) {
  let submitted: unknown;
  const cards = { [initial.id]: initial, ...alternatives };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/revisions")) {
        submitted = JSON.parse(String(init?.body));
        return Response.json({
          run_id: "revision",
          state: "ready",
          card_only: true,
        });
      }
      const id = decodeURIComponent(
        url.match(/\/cards\/([^/?]+)/)?.[1] ?? initial.id,
      );
      const card = cards[id] ?? initial;
      if (url.endsWith("/draft"))
        return Response.json({
          card,
          version: 2,
          source_current: true,
          superseded: false,
          card_only: true,
        });
      return Response.json({
        ...card,
        book_id: "book",
        version: 2,
        source_current: true,
        card_only: true,
        human_notes: "仅供用户自己的备注，不是原文或档案论据。",
      });
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
  return { ...render(view(initial.id)), view, submitted: () => submitted };
}

async function replace(
  label: string,
  value: string,
  container: Pick<typeof screen, "getByRole"> = screen,
) {
  const input = container.getByRole("textbox", { name: label });
  await userEvent.clear(input);
  await userEvent.type(input, value);
}

test("v7 dossier edits submit the complete source-bound dossier and citation without changing other card fields", async () => {
  const fixture = contract();
  const { submitted } = mountEditor(fixture.initial);
  await screen.findByRole("textbox", { name: "修订内容提要" });
  await userEvent.click(screen.getByText("史料档案说明"));
  await userEvent.click(screen.getByText("研究主题与范围"));
  await replace("研究主题与范围记载", "1936年调查报告与经费申请的文书范围");
  await replace("研究主题与范围说明", "不将申请写成拨付结果。");
  await userEvent.click(screen.getByText("可用论证单元"));
  await userEvent.click(screen.getByText(/^论证单元 1 ·/));
  await replace(
    "论证单元 1 论点",
    "120元为所申请的经费，本文不能确认实际拨付。",
  );
  await replace("论证单元 1 适用边界", "仅就此函的申请语句而言。");
  await replace("论证单元 1 互证需求", "需核对收款凭证及复函。");
  await userEvent.click(screen.getByText("规范引注"));
  await userEvent.click(screen.getByText("简短引注"));
  await replace("简短引注记载", "李四：《调查札记》，1936年");
  await userEvent.click(screen.getByText("印刷页码"));
  const pages = within(screen.getByRole("group", { name: "印刷页码" }));
  await userEvent.selectOptions(
    pages.getByRole("combobox", { name: "记载状态" }),
    "unknown",
  );
  expect(
    (pages.getByRole("textbox", { name: "印刷页码记载" }) as HTMLInputElement)
      .value,
  ).toBe("");
  await replace("印刷页码说明", "印刷页码待核对，暂不用于精确页码引用。");
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "引注完整度" }),
    "partial",
  );
  await replace("引注待补字段", "印刷页码核对");
  await userEvent.click(screen.getByRole("button", { name: "提交修订核验" }));
  await screen.findByText("修订已提交独立核验，原有卡片和人工决定继续保留。");
  expect(submitted()).toEqual({
    expected_version: 2,
    card: { ...fixture.initial, dossier: fixture.edited_dossier },
  });
  expect(JSON.stringify(submitted())).not.toContain("仅供用户自己的备注");
});

test("dossier sections and their objects mount only visible controls and preserve a folded draft", async () => {
  const initial = contract().initial;
  initial.evidence.push(
    ...Array.from({ length: 73 }, (_, index) => ({
      ...initial.evidence[index % 3],
      id: `extra-${index}`,
    })),
  );
  initial.dossier.argument_units = Array.from({ length: 63 }, (_, index) => ({
    ...initial.dossier.argument_units[0],
    id: `unit-${index}`,
    title: `用途${index}`,
  }));
  mountEditor(initial);
  await screen.findByRole("textbox", { name: "修订内容提要" });
  const controls = () =>
    document.querySelectorAll("input, textarea, select").length;
  expect(controls()).toBeLessThanOrEqual(30);
  await userEvent.click(screen.getByText("史料档案说明"));
  expect(controls()).toBeLessThanOrEqual(30);
  expect(screen.queryByRole("checkbox")).toBeNull();
  await userEvent.click(screen.getByText("可用论证单元"));
  await screen.findByText("论证单元 63 · 用途62");
  expect(controls()).toBeLessThanOrEqual(30);
  await userEvent.click(screen.getByText("论证单元 1 · 用途0"));
  expect(screen.getAllByRole("checkbox")).toHaveLength(76);
  expect(controls()).toBeLessThan(110);
  await replace("论证单元 1 论点", "折叠前保留的有据草稿");
  await userEvent.click(screen.getByText("可用论证单元"));
  expect(screen.queryByRole("textbox", { name: "论证单元 1 论点" })).toBeNull();
  expect(screen.queryByRole("checkbox")).toBeNull();
  await userEvent.click(screen.getByText("可用论证单元"));
  await userEvent.click(screen.getByText("论证单元 1 · 用途0"));
  expect(
    (
      screen.getByRole("textbox", {
        name: "论证单元 1 论点",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe("折叠前保留的有据草稿");
});

test("switching card IDs resets dossier values and open sections instead of carrying another card's draft", async () => {
  const first = contract().initial;
  const second = structuredClone(first);
  second.id = "second";
  second.summary = "第二张卡片的原文摘要";
  second.dossier.research_scope.value = "第二张卡片的研究范围";
  const editor = mountEditor(first, { second });
  await screen.findByRole("textbox", { name: "修订内容提要" });
  await userEvent.click(screen.getByText("史料档案说明"));
  await userEvent.click(screen.getByText("研究主题与范围"));
  await replace("研究主题与范围记载", "尚未提交的第一张档案草稿");
  editor.rerender(editor.view("second"));
  await screen.findByDisplayValue("第二张卡片的原文摘要");
  expect(screen.queryByDisplayValue("尚未提交的第一张档案草稿")).toBeNull();
  expect(
    screen.queryByRole("textbox", { name: "研究主题与范围记载" }),
  ).toBeNull();
  await userEvent.click(screen.getByText("史料档案说明"));
  await userEvent.click(screen.getByText("研究主题与范围"));
  await screen.findByDisplayValue("第二张卡片的研究范围");
  expect(editor.submitted()).toBeUndefined();
});

test.each(["source", "unit", "citation", "citation-fields"] as const)(
  "an invalid folded dossier %s reveals only the section that needs attention",
  async (kind) => {
    const initial = contract().initial;
    const target = {
      source: "研究主题与范围记载",
      unit: "论证单元 1 适用边界",
      citation: "完整引注记载",
      "citation-fields": "引注待补字段",
    }[kind];
    if (kind === "source") initial.dossier.research_scope.evidence_ids = [];
    else if (kind === "unit")
      initial.dossier.argument_units[0].applicability_limit = "";
    else if (kind === "citation")
      initial.dossier.citation.full = {
        value: "",
        certainty: "unknown",
        evidence_ids: [],
        note: "",
      };
    else {
      initial.dossier.citation.completeness = "partial";
      initial.dossier.citation.missing_fields = ["印刷页码", "印刷页码"];
    }
    const editor = mountEditor(initial);
    await screen.findByRole("textbox", { name: "修订内容提要" });
    expect(screen.queryByRole("textbox", { name: target })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "提交修订核验" }));
    await screen.findByRole("textbox", { name: target });
    expect(editor.submitted()).toBeUndefined();
    expect(
      document.querySelectorAll("input, textarea, select").length,
    ).toBeLessThan(50);
    expect(
      screen.getByRole("button", { name: "定位需要补充的位置" }),
    ).toBeTruthy();
  },
);

test("unknown dossier facts and citation fields stay empty without invented notes, dates or printed pages", async () => {
  const initial = contract().initial;
  for (const field of [
    "research_scope",
    "formation_background",
    "text_structure",
    "source_nature",
    "source_role",
    "research_value",
    "can_support",
    "cannot_support",
    "needs_corroboration",
  ] as const)
    initial.dossier[field] = {
      value: "",
      certainty: "unknown",
      evidence_ids: [],
      note: "",
    };
  initial.dossier.argument_units = [];
  initial.dossier.argument_units_note = "现有节选不足以划分独立论证单元。";
  initial.dossier.citation = {
    full: { value: "", certainty: "unknown", evidence_ids: [], note: "" },
    short: { value: "", certainty: "unknown", evidence_ids: [], note: "" },
    printed_pages: {
      value: "",
      certainty: "unknown",
      evidence_ids: [],
      note: "",
    },
    publication_date: {
      text: "",
      normalized: null,
      certainty: "unknown",
      evidence_ids: [],
    },
    completeness: "unknown",
    missing_fields: [],
  };
  const editor = mountEditor(initial);
  await screen.findByRole("textbox", { name: "修订内容提要" });
  await userEvent.click(screen.getByRole("button", { name: "提交修订核验" }));
  await screen.findByText("修订已提交独立核验，原有卡片和人工决定继续保留。");
  expect(editor.submitted()).toEqual({ expected_version: 2, card: initial });
});

test("a retained v6 card has no dossier editor and keeps its complete original payload unchanged", async () => {
  const initial = JSON.parse(
    readFileSync(
      resolve(
        process.cwd(),
        "src/features/research/card-revision-contract.fixture.json",
      ),
      "utf8",
    ),
  ).initial as Draft;
  const editor = mountEditor(initial);
  await screen.findByRole("textbox", { name: "修订内容提要" });
  expect(screen.queryByText("史料档案说明")).toBeNull();
  expect(screen.queryByText("可用论证单元")).toBeNull();
  expect(screen.queryByText("规范引注")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "提交修订核验" }));
  await screen.findByText("修订已提交独立核验，原有卡片和人工决定继续保留。");
  expect(editor.submitted()).toEqual({ expected_version: 2, card: initial });
});

test("adding a usable argument unit preserves its source evidence and permits an empty corroboration request", async () => {
  const fixture = contract();
  const editor = mountEditor(fixture.initial);
  vi.spyOn(crypto, "randomUUID").mockReturnValue(
    "3456789a-0000-0000-0000-000000000000",
  );
  await screen.findByRole("textbox", { name: "修订内容提要" });
  await userEvent.click(screen.getByText("可用论证单元"));
  await userEvent.click(screen.getByRole("button", { name: "添加论证单元" }));
  const group = within(screen.getByRole("group", { name: "论证单元 2" }));
  await replace("论证单元 2 标题", fixture.new_unit.title, group);
  await replace("论证单元 2 论点", fixture.new_unit.claim, group);
  await replace(
    "论证单元 2 适用边界",
    fixture.new_unit.applicability_limit,
    group,
  );
  await userEvent.click(group.getByRole("checkbox", { name: /编者按/ }));
  await userEvent.click(screen.getByRole("button", { name: "提交修订核验" }));
  await screen.findByText("修订已提交独立核验，原有卡片和人工决定继续保留。");
  expect(editor.submitted()).toEqual({
    expected_version: 2,
    card: {
      ...fixture.initial,
      dossier: {
        ...fixture.initial.dossier,
        argument_units: [
          ...fixture.initial.dossier.argument_units,
          fixture.new_unit,
        ],
      },
    },
  });
});

test("removing all usable argument units retains the full dossier and requires a source limitation before submission", async () => {
  const initial = contract().initial;
  const editor = mountEditor(initial);
  await screen.findByRole("textbox", { name: "修订内容提要" });
  await userEvent.click(screen.getByText("可用论证单元"));
  await userEvent.click(screen.getByText(/^论证单元 1 ·/));
  await userEvent.click(screen.getByRole("button", { name: "移除论证单元 1" }));
  await userEvent.click(screen.getByText("可用论证单元"));
  await userEvent.click(screen.getByRole("button", { name: "提交修订核验" }));
  await screen.findByText("未列论证单元时，请说明这份来源的适用限制。");
  expect(editor.submitted()).toBeUndefined();
  await replace(
    "未列论证单元说明",
    "现有节选不足以单列论证单元，保留原有来源事实。",
  );
  await userEvent.click(screen.getByRole("button", { name: "提交修订核验" }));
  await screen.findByText("修订已提交独立核验，原有卡片和人工决定继续保留。");
  expect(editor.submitted()).toEqual({
    expected_version: 2,
    card: {
      ...initial,
      dossier: {
        ...initial.dossier,
        argument_units: [],
        argument_units_note: "现有节选不足以单列论证单元，保留原有来源事实。",
      },
    },
  });
});
