// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { components } from "@/client/schema";
import type { ResearchEvidence } from "./api";
import { CrossRelationFields } from "./CardCitationFields";
import type { Draft } from "./CardFields";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const publication = "a".repeat(64);
const relation: components["schemas"]["CrossCardRelation"] = {
  id: "cross",
  target_card_id: "target",
  target_card_version: 3,
  target_publication_sha256: publication,
  kind: "background",
  reason: "不同阶段的背景",
  certainty: "inferred",
  source_evidence_ids: ["own"],
  target_evidence_ids: [],
};
const own = {
  id: "own",
  article_id: "primary",
  start: 0,
  end: 4,
  quote: "本篇材料",
  footnote_ids: [],
};
const copied = {
  id: "draft-copy",
  article_id: "known-target-alias",
  start: 7,
  end: 11,
  quote: "新草稿中补充的对方原文",
  footnote_ids: [],
};
const knownSource: ResearchEvidence = {
  ...copied,
  id: "retained-id",
  pages: [7],
  book_id: "target-book",
  publication_sha256: publication,
  original_article_id: "target-article",
  text_sha256: "b".repeat(64),
};
const target = {
  id: "target",
  title: "目标卡片",
  book_id: "target-book",
  article_id: "target-article",
  version: 3,
  publication_sha256: publication,
  source_current: true,
  superseded: false,
  state: "verified",
  evidence: [
    { ...copied, id: "counterpart-local", quote: "对方自己的局部编号" },
  ],
};

function editor(
  evidence: Draft["evidence"],
  sources = [knownSource],
  retainedRelations?: components["schemas"]["CrossCardRelation"][],
) {
  const onChange = vi.fn();
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = (next: Draft["evidence"], shownRelation = relation) => (
    <MemoryRouter>
      <QueryClientProvider client={query}>
        <CrossRelationFields
          value={[shownRelation]}
          evidence={next}
          sourceEvidence={sources}
          retainedRelations={retainedRelations}
          onChange={onChange}
        />
      </QueryClientProvider>
    </MemoryRouter>
  );
  return { ...render(view(evidence)), onChange, view };
}

test("target choices follow newly edited draft evidence and known source aliases, not retained evidence IDs", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(target)),
  );
  const rendered = editor([own, { ...copied, article_id: "primary" }]);
  await userEvent.click(screen.getByText("跨卡文书及证据关系"));
  await screen.findByRole("group", { name: "目标卡片" });
  expect(screen.queryByRole("group", { name: "对方卡片依据" })).toBeNull();

  rendered.rerender(rendered.view([own, copied]));
  const choices = within(screen.getByRole("group", { name: "对方卡片依据" }));
  await userEvent.click(
    choices.getByRole("checkbox", { name: /新草稿中补充的对方原文/ }),
  );

  expect(rendered.onChange).toHaveBeenLastCalledWith([
    { ...relation, target_evidence_ids: ["draft-copy"] },
  ]);
  expect(
    choices.queryByRole("checkbox", { name: /对方自己的局部编号/ }),
  ).toBeNull();
});

test("an existing binding keeps its registered source alias while allowing new local IDs in the draft", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(target)),
  );
  const currentNoteSource = {
    ...knownSource,
    id: "current-notes-id",
    article_id: "different-note-variant",
    quote: "同原文的另一注释版本",
  };
  const rendered = editor(
    [own, copied, currentNoteSource],
    [knownSource, currentNoteSource],
    [{ ...relation, target_evidence_ids: ["retained-id"] }],
  );
  await userEvent.click(screen.getByText("跨卡文书及证据关系"));
  const choices = within(
    await screen.findByRole("group", { name: "对方卡片依据" }),
  );
  expect(choices.getAllByRole("checkbox")).toHaveLength(1);
  await userEvent.click(
    choices.getByRole("checkbox", { name: /新草稿中补充的对方原文/ }),
  );
  expect(rendered.onChange).toHaveBeenLastCalledWith([
    { ...relation, target_evidence_ids: ["draft-copy"] },
  ]);
  rendered.rerender(
    rendered.view([own, { ...copied, article_id: "different-note-variant" }]),
  );
  expect(screen.queryByRole("group", { name: "对方卡片依据" })).toBeNull();
});

test("retargeting a relation does not reuse the previous target's registered source alias", async () => {
  const replacement = {
    ...target,
    id: "replacement",
    title: "另一个目标",
    book_id: "replacement-book",
    article_id: "replacement-article",
    version: 4,
    publication_sha256: "c".repeat(64),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      Response.json(
        String(input).endsWith("/replacement") ? replacement : target,
      ),
    ),
  );
  const newSource = {
    ...knownSource,
    id: "new-source",
    article_id: "replacement-alias",
    book_id: replacement.book_id,
    original_article_id: replacement.article_id,
    publication_sha256: replacement.publication_sha256,
    quote: "新目标的保留原文",
  };
  const rendered = editor(
    [own, copied, newSource],
    [knownSource, newSource],
    [{ ...relation, target_evidence_ids: ["retained-id"] }],
  );
  await userEvent.click(screen.getByText("跨卡文书及证据关系"));
  await screen.findByRole("group", { name: "目标卡片" });
  rendered.rerender(
    rendered.view([own, copied, newSource], {
      ...relation,
      target_card_id: replacement.id,
      target_card_version: replacement.version,
      target_publication_sha256: replacement.publication_sha256,
    }),
  );
  await screen.findByRole("group", { name: "另一个目标" });
  const choices = within(screen.getByRole("group", { name: "对方卡片依据" }));
  expect(choices.getAllByRole("checkbox")).toHaveLength(1);
  expect(
    choices.getByRole("checkbox", { name: /新目标的保留原文/ }),
  ).toBeTruthy();
});

test("target choices exclude owner citations mapped to another book, publication, or original article", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(target)),
  );
  const others = [
    {
      ...knownSource,
      id: "wrong-book",
      article_id: "book-alias",
      book_id: "another-book",
      quote: "他书内容",
    },
    {
      ...knownSource,
      id: "wrong-publication",
      article_id: "pub-alias",
      publication_sha256: "c".repeat(64),
      quote: "另版内容",
    },
    {
      ...knownSource,
      id: "wrong-article",
      article_id: "article-alias",
      original_article_id: "another-article",
      quote: "同书另篇",
    },
  ];
  editor([own, copied, ...others], [knownSource, ...others]);
  await userEvent.click(screen.getByText("跨卡文书及证据关系"));
  const choices = within(
    await screen.findByRole("group", { name: "对方卡片依据" }),
  );
  expect(choices.getAllByRole("checkbox")).toHaveLength(1);
  expect(
    choices.getByRole("checkbox", { name: /新草稿中补充的对方原文/ }),
  ).toBeTruthy();
  expect(
    within(screen.getByRole("group", { name: "本卡依据" })).getAllByRole(
      "checkbox",
    ),
  ).toHaveLength(5);
});

test("a new relation without a retained target quote explains how to add evidence instead of offering foreign local IDs", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(target)),
  );
  editor([own], []);
  await userEvent.click(screen.getByText("跨卡文书及证据关系"));
  await screen.findByRole("group", { name: "目标卡片" });
  expect(
    screen.getByText(/请先在本卡中补充并核对对方冻结版本的原文/),
  ).toBeTruthy();
  expect(screen.queryByRole("group", { name: "对方卡片依据" })).toBeNull();
  expect(
    screen.queryByRole("checkbox", { name: /对方自己的局部编号/ }),
  ).toBeNull();
});

test.each([
  { version: 4 },
  { publication_sha256: "c".repeat(64) },
  { source_current: false },
  { superseded: true },
  { state: "pending" },
  { id: "another-card" },
])(
  "unavailable or changed target cannot offer evidence for an edit: %j",
  async (changed) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ ...target, ...changed })),
    );
    editor([own, copied]);
    await userEvent.click(screen.getByText("跨卡文书及证据关系"));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByRole("group", { name: "对方卡片依据" })).toBeNull();
  },
);
