// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { components } from "@/client/schema";
import type { ResearchEvidence } from "./api";
import CrossCardRelations from "./CrossCardRelations";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const publication = "a".repeat(64);
const targetText = "b".repeat(64);
const relation: components["schemas"]["CrossCardRelation"] = {
  id: "relation",
  target_card_id: "target-card",
  target_card_version: 3,
  target_publication_sha256: publication,
  kind: "background",
  reason: "两份材料分别说明组织准备与后续发展",
  certainty: "inferred",
  source_evidence_ids: ["source-local"],
  target_evidence_ids: ["copied-target-local"],
};
const ownSource: ResearchEvidence = {
  id: "source-local",
  article_id: "own-article",
  start: 0,
  end: 4,
  quote: "本篇来源",
  pages: [2],
  footnote_ids: [],
  book_id: "own-book",
  text_sha256: "c".repeat(64),
};
const copiedTarget: ResearchEvidence = {
  id: "copied-target-local",
  article_id: "frozen-target-alias",
  original_article_id: "target-article",
  start: 41,
  end: 48,
  quote: "目标版本保留引文",
  pages: [7],
  footnote_ids: [],
  book_id: "target-book",
  publication_sha256: publication,
  text_sha256: targetText,
  reading_target: {
    chapter_id: "target-article",
    run_id: "target-source-run",
    physical_page: 7,
    start: 41,
    end: 48,
    text_sha256: targetText,
    image_sha256: "d".repeat(64),
    anchor: "目标版本保留引文",
  },
};
const targetCard = {
  id: "target-card",
  title: "目标卡片",
  book_id: "target-book",
  article_id: "target-article",
  version: 3,
  publication_sha256: publication,
  source_current: true,
  superseded: false,
  evidence: [
    { ...copiedTarget, id: "target-own-local", quote: "对方另一个局部编号" },
  ],
};

function renderRelations(
  evidence = [ownSource, copiedTarget],
  shownRelation = relation,
) {
  render(
    <MemoryRouter initialEntries={["/research?card=owner-card"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <CrossCardRelations
          relations={[shownRelation]}
          evidence={evidence}
          bookId="own-book"
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

test("cross-card evidence keeps the owner's local references and frozen target reading location", async () => {
  const fetch = vi.fn(async () => Response.json(targetCard));
  vi.stubGlobal("fetch", fetch);
  renderRelations();

  await userEvent.click(screen.getByRole("button", { name: "对照双方证据" }));

  expect(await screen.findByText("目标版本保留引文")).toBeTruthy();
  expect(screen.getByText("本篇来源")).toBeTruthy();
  expect(screen.queryByText("对方另一个局部编号")).toBeNull();
  expect(fetch).toHaveBeenCalledWith(
    "/api/v2/research/cards/target-card",
    expect.objectContaining({ signal: expect.anything() }),
  );
  const href = screen
    .getByRole("link", { name: "阅读原文 · PDF 第 7 页" })
    .getAttribute("href")!;
  const destination = new URL(href, "http://localhost");
  expect(destination.pathname).toBe("/books/target-book");
  expect(destination.searchParams.get("chapter")).toBe("target-article");
  expect(destination.searchParams.get("run")).toBe("target-source-run");
  expect(destination.searchParams.get("textSha")).toBe(targetText);
  expect(destination.searchParams.get("start")).toBe("41");
  expect(destination.searchParams.get("end")).toBe("48");
});

test("identical evidence IDs in two cards do not substitute a counterpart's unrelated quote", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        ...targetCard,
        evidence: [
          { ...copiedTarget, id: "e1", quote: "对方卡内同号但无关的引文" },
        ],
      }),
    ),
  );
  renderRelations([ownSource, { ...copiedTarget, id: "e1" }], {
    ...relation,
    target_evidence_ids: ["e1"],
  });

  await userEvent.click(screen.getByRole("button", { name: "对照双方证据" }));

  expect(await screen.findByText("目标版本保留引文")).toBeTruthy();
  expect(screen.queryByText("对方卡内同号但无关的引文")).toBeNull();
});

test.each([
  { version: 4, publication_sha256: publication },
  { version: 3, publication_sha256: "e".repeat(64) },
  { version: 3, publication_sha256: publication, id: "another-card" },
])(
  "changed target version or publication cannot authorize copied quotes: %j",
  async (changed) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ ...targetCard, ...changed })),
    );
    renderRelations();

    await userEvent.click(screen.getByRole("button", { name: "对照双方证据" }));

    expect(await screen.findByText(/对方卡片版本已有变化/)).toBeTruthy();
    expect(screen.queryByText("目标版本保留引文")).toBeNull();
  },
);

test.each([404, 409])(
  "unavailable frozen target (%i) does not show copied evidence as available",
  async (status) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ detail: "保留目标版本不可用" }, { status }),
      ),
    );
    renderRelations();

    await userEvent.click(screen.getByRole("button", { name: "对照双方证据" }));

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText("目标版本保留引文")).toBeNull();
    expect(
      screen.getByRole("button", { name: "重新读取对方证据" }),
    ).toBeTruthy();
  },
);

test("retained evidence of a superseded source still labels its reader link as historical", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ ...targetCard, source_current: false, superseded: true }),
    ),
  );
  renderRelations();

  await userEvent.click(screen.getByRole("button", { name: "对照双方证据" }));

  expect(await screen.findByText("目标版本保留引文")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "阅读历史原文 · PDF 第 7 页" }),
  ).toBeTruthy();
  expect(
    screen.queryByRole("link", { name: "阅读原文 · PDF 第 7 页" }),
  ).toBeNull();
});
