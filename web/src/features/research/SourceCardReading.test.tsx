// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useNavigate } from "react-router-dom";
import ResearchDetail from "./ResearchDetail";
import * as libraryApi from "@/client/api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
const sourceValue = (value: string) => ({
  value,
  certainty: "stated",
  evidence_ids: ["e0"],
  note: "",
});
function sourceCard() {
  return {
    id: "whole-document",
    display_id: "CARD-WHOLE",
    authors: "甲作者",
    book_id: "book",
    book_title: "来源汇编",
    run_id: "run",
    article_id: "article",
    title: "一份完整文献的题名",
    summary: "来源阅读提要",
    version: 2,
    state: "pending",
    source_current: true,
    card_only: true,
    document_type: "报告",
    source_layer: "汇编正文",
    pages: [9, 10, 11],
    printed_pages: "12—14",
    metadata: { title_origin: "source", authors: sourceValue("甲作者") },
    items: [
      {
        id: "topic1",
        kind: "evidence",
        title: "第一个主题",
        text: "第一部分原文内容",
        evidence_ids: ["e0"],
        limitations: [],
      },
      {
        id: "topic2",
        kind: "argument",
        title: "第二个主题",
        text: "原作者提出的判断",
        attribution: "甲作者",
        evidence_ids: ["e75"],
        limitations: [],
      },
    ],
    evidence: Array.from({ length: 76 }, (_, index) => ({
      id: `e${index}`,
      article_id: "article",
      start: index * 3,
      end: index * 3 + 3,
      quote: `独立原文引句${index}`,
      pages: [index === 75 ? 11 : 9],
      footnote_ids: [],
      reading_target: {
        chapter_id: "article",
        run_id: "source",
        physical_page: index === 75 ? 11 : 9,
        start: index * 3,
        end: index * 3 + 3,
        text_sha256: "a".repeat(64),
        image_sha256: "b".repeat(64),
        anchor: `独立原文引句${index}`,
      },
    })),
    mentions: [],
    events: [],
    relations: [],
    tags: [],
    limitations: ["不能由申请推定已经实行。"],
    alternatives: [],
    questions: [],
    dossier: {
      research_scope: sourceValue("研究对象仅限本篇所述事项。"),
      formation_background: sourceValue("形成于材料所记的语境。"),
      text_structure: sourceValue("先叙述，后提出主张。"),
      source_nature: sourceValue("当时报告的汇编文本。"),
      source_role: sourceValue("记录形成者的行动与判断。"),
      research_value: sourceValue("可观察原作者的论述方式。"),
      can_support: sourceValue("可支持此文本提出过该主张。"),
      cannot_support: sourceValue("不能直接证明主张已实现。"),
      needs_corroboration: sourceValue("实施结果须另查同期材料。"),
      argument_units: [
        {
          id: "unit1",
          title: "论证单元甲",
          claim: "文本中的行动请求具有明确指向。",
          evidence_ids: ["e0"],
          applicability_limit: "仅限形成者主张",
          corroboration_needed: "实施记录",
        },
      ],
      argument_units_note: "",
      citation: {
        full: sourceValue("甲作者：《该报告》，《来源汇编》，第12—14页。"),
        short: sourceValue("甲作者：《该报告》"),
        printed_pages: sourceValue("12—14"),
        publication_date: {
          text: "1991年",
          certainty: "stated",
          evidence_ids: ["e0"],
        },
        completeness: "partial",
        missing_fields: ["出版地"],
      },
    },
  };
}
function mount(
  card: object = sourceCard(),
  url = "/books/book?tab=research&card=whole-document",
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(card)),
  );
  return render(
    <MemoryRouter initialEntries={[url]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchDetail />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

test("one complete document has chapter navigation and ordered H3 themes, with evidence and human actions outside default reading", async () => {
  mount();
  await screen.findByRole("heading", { name: "一份完整文献的题名" });
  expect(screen.getByRole("navigation", { name: "本卡章节" })).toBeTruthy();
  const core = screen.getByRole("region", { name: "核心内容" });
  expect(
    within(core)
      .getAllByRole("heading", { level: 3 })
      .map((node) => node.textContent),
  ).toEqual(["第一个主题", "第二个主题"]);
  expect(screen.queryByRole("button", { name: "确认采用此卡" })).toBeNull();
  expect(document.querySelectorAll(".research-evidence > article").length).toBe(
    0,
  );
  expect(screen.queryByText("独立原文引句75")).toBeNull();
  expect(screen.getByText("研究对象仅限本篇所述事项。")).toBeTruthy();
  expect(screen.getByText("原作者提出的判断")).toBeTruthy();
});

test.each([
  ["pending", true, true],
  ["verified", true, false],
  ["pending", false, false],
] as const)(
  "review actions require explicit review mode and a current pending card: %s/%s",
  async (state, current, allowed) => {
    mount({ ...sourceCard(), state, source_current: current });
    await screen.findByRole("heading", { name: "一份完整文献的题名" });
    expect(screen.queryByRole("button", { name: "确认采用此卡" })).toBeNull();
    expect(screen.queryByRole("button", { name: "修订完整卡片" })).toBeNull();
    await userEvent.click(
      screen.getByRole("button", {
        name: current ? "核对本卡" : "查看历史核验",
      }),
    );
    expect(
      Boolean(screen.queryByRole("button", { name: "确认采用此卡" })),
    ).toBe(allowed);
    expect(
      Boolean(screen.queryByRole("button", { name: "修订完整卡片" })),
    ).toBe(current);
    await userEvent.click(screen.getByRole("button", { name: "阅读本卡" }));
    expect(screen.queryByRole("button", { name: "确认采用此卡" })).toBeNull();
  },
);

test("a legacy card keeps its original themes and explicit missing dossier fields instead of deriving new analysis from keywords", async () => {
  mount({
    ...sourceCard(),
    dossier: null,
    dossier_available: false,
    metadata: null,
    tags: ["旧卡原有主题词"],
    items: [
      {
        id: "old-topic",
        kind: "argument",
        title: "研究对象关键词不等于已整理说明",
        text: "旧卡原作者的观点仍完整保留。",
        evidence_ids: ["e0"],
      },
    ],
  });
  await screen.findByText("旧卡原作者的观点仍完整保留。");
  expect(
    screen.getByText(
      "原有卡片未单列研究对象、形成背景和文本结构；请结合提要和原文阅读。",
    ),
  ).toBeTruthy();
  expect(
    screen.getByText("本卡未制作独立论证单元。原作者的观点保留在核心内容中。"),
  ).toBeTruthy();
  expect(screen.queryByText("研究对象仅限本篇所述事项。")).toBeNull();
  expect(screen.getByText("已有主题词：旧卡原有主题词")).toBeTruthy();
  expect(screen.getByRole("link", { name: "导出 Markdown" })).toBeTruthy();
  const chapters = Array.from(
    document.querySelectorAll(".dossier-reading > section > h2"),
  );
  expect(chapters.at(-1)?.textContent).toBe("我的备注");
});

test("legacy PDF page fallback uses only this document's source rather than copied external citations", async () => {
  const card = sourceCard();
  mount({
    ...card,
    pages: [],
    evidence: [
      ...card.evidence,
      {
        ...card.evidence[0],
        id: "external",
        article_id: "copied-foreign-alias",
        pages: [999],
      },
    ],
  });
  await screen.findByRole("heading", { name: card.title });
  const basic = screen.getByRole("region", { name: "基本资料" });
  expect(basic.textContent).not.toContain("999");
  expect(within(basic).getByText("9、11")).toBeTruthy();
});

test.each([
  ["complete", "完整引注"],
  ["partial", "已知出处（尚未齐备）"],
  ["unknown", "出处未详"],
] as const)(
  "citation heading describes its declared completeness without claiming an incomplete record is complete: %s",
  async (completeness, heading) => {
    const card = sourceCard();
    const citation = card.dossier.citation;
    mount({
      ...card,
      dossier: {
        ...card.dossier,
        citation: {
          ...citation,
          completeness,
          full:
            completeness === "unknown"
              ? {
                  value: "",
                  certainty: "unknown",
                  evidence_ids: [],
                  note: "未取得可靠书目信息。",
                }
              : citation.full,
        },
      },
    });
    await screen.findByRole("heading", { name: card.title });
    const chapter = screen.getByRole("region", { name: "规范引注与页码" });
    expect(
      within(chapter).getByRole("heading", { name: heading }),
    ).toBeTruthy();
    if (completeness !== "complete")
      expect(
        within(chapter).queryByRole("heading", { name: "完整引注" }),
      ).toBeNull();
  },
);

test("closing an evidence panel returns to its reading chapter when the source index was collapsed in the meantime", async () => {
  const scrolled = vi
    .spyOn(Element.prototype, "scrollIntoView")
    .mockImplementation(() => {});
  vi.spyOn(libraryApi, "readOriginalPage").mockResolvedValue(
    "/source-page.png",
  );
  mount();
  await screen.findByRole("heading", { name: "一份完整文献的题名" });
  await userEvent.click(screen.getByText("引文与页码索引 · 76 条"));
  await userEvent.click(
    await screen.findByRole("button", { name: "查看证据 76" }),
  );
  await screen.findByText("独立原文引句75");
  await userEvent.click(screen.getByText("引文与页码索引 · 76 条"));
  await userEvent.click(
    screen.getByRole("button", { name: "关闭引文，返回阅读位置" }),
  );
  expect(scrolled.mock.instances.at(-1)).toBe(
    screen.getByRole("region", { name: "规范引注与页码" }),
  );
});

function NextDocument() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate("/books/book?tab=research&card=second")}>
      打开下一份文献
    </button>
  );
}
test("switching documents closes the prior quote panel even when both use the same local evidence IDs", async () => {
  const first = sourceCard();
  const second = {
    ...sourceCard(),
    id: "second",
    title: "另一份完整文献",
    evidence: sourceCard().evidence.map((span) => ({
      ...span,
      quote: `另一文献的${span.id}`,
      reading_target: { ...span.reading_target, run_id: "other-source" },
    })),
  };
  vi.spyOn(libraryApi, "readOriginalPage").mockResolvedValue(
    "/source-page.png",
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      Response.json(String(input).includes("/cards/second") ? second : first),
    ),
  );
  render(
    <MemoryRouter
      initialEntries={["/books/book?tab=research&card=whole-document"]}
    >
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchDetail />
        <NextDocument />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: first.title });
  await userEvent.click(
    within(screen.getByRole("region", { name: "核心内容" })).getByRole(
      "button",
      { name: "证据 76" },
    ),
  );
  await screen.findByText("独立原文引句75");
  await userEvent.click(screen.getByRole("button", { name: "打开下一份文献" }));
  await screen.findByRole("heading", { name: second.title });
  expect(screen.queryByRole("complementary", { name: "引文核对" })).toBeNull();
  expect(screen.queryByText("独立原文引句75")).toBeNull();
  expect(screen.queryByText("另一文献的e75")).toBeNull();
});

test("chapter navigation scrolls within this card and a requested citation opens only its exact source page, then returns focus", async () => {
  const scroll = vi
    .spyOn(Element.prototype, "scrollIntoView")
    .mockImplementation(() => {});
  const original = vi
    .spyOn(libraryApi, "readOriginalPage")
    .mockResolvedValue("/source-page.png");
  mount();
  await screen.findByRole("heading", { name: "一份完整文献的题名" });
  await userEvent.click(
    within(screen.getByRole("navigation", { name: "本卡章节" })).getByRole(
      "button",
      { name: /核心内容/ },
    ),
  );
  expect(scroll.mock.instances[0]).toBe(
    screen.getByRole("region", { name: "核心内容" }),
  );
  const citation = within(
    screen.getByRole("region", { name: "核心内容" }),
  ).getByRole("button", { name: "证据 76" });
  await userEvent.click(citation);
  await screen.findByText("独立原文引句75");
  expect(document.querySelectorAll(".research-evidence > article").length).toBe(
    1,
  );
  expect(screen.queryByText("独立原文引句0")).toBeNull();
  const link = screen.getByRole("link", { name: /阅读原文.*11/ });
  expect(
    new URL(link.getAttribute("href")!, "http://local").searchParams.get(
      "physicalPage",
    ),
  ).toBe("11");
  await vi.waitFor(() => expect(original).toHaveBeenCalled());
  expect(original.mock.calls[0][0]).toBe("source");
  expect(original.mock.calls[0][1]).toBe(11);
  await userEvent.click(
    screen.getByRole("button", { name: "关闭引文，返回阅读位置" }),
  );
  expect(screen.queryByRole("complementary", { name: "引文核对" })).toBeNull();
  expect(document.activeElement).toBe(citation);
});
