// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import CardEditor from "./CardEditor";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test.each([
  { id: "target-own-local", quote: "对方保留原文" },
  { id: "copied-target-local", quote: "对方同号但无关引文" },
])(
  "editing a cross-card relation saves the copied quote's owner-local evidence ID: %j",
  async (counterpart) => {
    const publication = "a".repeat(64);
    const draft = {
      id: "owner-card",
      article_id: "owner-article",
      title: "组织工作的回忆",
      summary: "原提要",
      document_type: "回忆",
      source_layer: "正文",
      items: [],
      evidence: [
        {
          id: "own",
          article_id: "owner-article",
          start: 0,
          end: 4,
          quote: "本篇来源",
          footnote_ids: [],
        },
        {
          id: "copied-target-local",
          article_id: "target-alias",
          start: 7,
          end: 11,
          quote: "对方保留原文",
          footnote_ids: [],
        },
      ],
      mentions: [],
      events: [],
      relations: [],
      identity_links: [],
      tags: [],
      limitations: [],
      alternatives: [],
      questions: [],
      metadata: null,
      cross_relations: [
        {
          id: "cross",
          target_card_id: "target-card",
          target_card_version: 3,
          target_publication_sha256: publication,
          kind: "background",
          reason: "两份材料描述不同阶段",
          certainty: "inferred",
          source_evidence_ids: ["own"],
          target_evidence_ids: [],
        },
      ],
    };
    let submitted: unknown;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/revisions")) {
          submitted = JSON.parse(String(init?.body));
          return Response.json({ run_id: "revision", state: "ready" });
        }
        if (url.endsWith("/draft"))
          return Response.json({
            card: draft,
            version: 2,
            source_current: true,
            superseded: false,
          });
        if (url.endsWith("/target-card"))
          return Response.json({
            id: "target-card",
            title: "对方原卡",
            book_id: "target-book",
            article_id: "target-article",
            version: 3,
            publication_sha256: publication,
            source_current: true,
            superseded: false,
            state: "verified",
            evidence: [
              {
                ...draft.evidence[1],
                ...counterpart,
                article_id: "target-article",
                pages: [7],
              },
            ],
          });
        return Response.json({
          ...draft,
          version: 2,
          book_id: "owner-book",
          source_current: true,
          superseded: false,
          evidence: draft.evidence.map((span) => ({
            ...span,
            book_id:
              span.article_id === "target-alias" ? "target-book" : "owner-book",
            original_article_id:
              span.article_id === "target-alias"
                ? "target-article"
                : "owner-article",
            publication_sha256:
              span.article_id === "target-alias" ? publication : "c".repeat(64),
          })),
        });
      }),
    );
    render(
      <MemoryRouter>
        <QueryClientProvider
          client={
            new QueryClient({ defaultOptions: { queries: { retry: false } } })
          }
        >
          <CardEditor cardId="owner-card" onClose={() => {}} />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    await screen.findByRole("textbox", { name: "修订内容提要" });
    await userEvent.click(screen.getByText("跨卡文书及证据关系"));
    const choices = within(
      await screen.findByRole("group", { name: "对方卡片依据" }),
    );
    await userEvent.click(
      choices.getByRole("checkbox", { name: /对方保留原文/ }),
    );
    await userEvent.click(screen.getByRole("button", { name: "提交修订核验" }));
    await screen.findByText("修订已提交独立核验，原有卡片和人工决定继续保留。");
    expect(submitted).toMatchObject({
      expected_version: 2,
      card: {
        cross_relations: [
          {
            target_card_id: "target-card",
            target_card_version: 3,
            target_publication_sha256: publication,
            source_evidence_ids: ["own"],
            target_evidence_ids: ["copied-target-local"],
          },
        ],
      },
    });
  },
);

test.each([true, false])(
  "revision conflicts preserve the draft and only allow explicit same-source version recovery when current=%s",
  async (sourceCurrent) => {
    const draft = {
      id: "c",
      article_id: "article",
      title: "报告",
      summary: "旧提要",
      document_type: "报告",
      source_layer: "正文",
      items: [],
      evidence: [
        {
          id: "e",
          article_id: "article",
          start: 0,
          end: 4,
          quote: "拨款申请",
          footnote_ids: [],
        },
      ],
      mentions: [],
      events: [],
      relations: [],
      identity_links: [],
      tags: [],
      limitations: [],
      alternatives: [],
      questions: [],
      metadata: null,
    };
    const requests: Array<{
      expected_version: number;
      card: { summary: string };
    }> = [];
    let conflicted = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/revisions")) {
          requests.push(JSON.parse(String(init?.body)));
          if (!conflicted) {
            conflicted = true;
            return Response.json(
              { detail: "卡片已更新，请核对当前版本。" },
              { status: 409 },
            );
          }
          return Response.json({ run_id: "revision", state: "ready" });
        }
        if (url.endsWith("/draft"))
          return Response.json({
            card: {
              ...draft,
              summary: conflicted ? "最新已保存提要" : draft.summary,
            },
            version: conflicted ? 4 : 3,
            source_current: conflicted ? sourceCurrent : true,
            superseded: false,
          });
        return Response.json({
          ...draft,
          state: "verified",
          version: 3,
          book_id: "book",
          run_id: "run",
          source_current: true,
          evidence: draft.evidence.map((span) => ({ ...span, pages: [1] })),
        });
      }),
    );
    render(
      <MemoryRouter>
        <QueryClientProvider
          client={
            new QueryClient({ defaultOptions: { queries: { retry: false } } })
          }
        >
          <CardEditor cardId="c" onClose={() => {}} />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    const summary = await screen.findByRole("textbox", {
      name: "修订内容提要",
    });
    await userEvent.clear(summary);
    await userEvent.type(summary, "保留我的完整修订草稿");
    await userEvent.click(screen.getByRole("button", { name: "提交修订核验" }));
    await screen.findByText(/卡片已更新，请核对当前版本/);
    await userEvent.click(screen.getByRole("button", { name: "核对当前卡片" }));
    await screen.findByText("最新已保存提要");
    expect((summary as HTMLTextAreaElement).value).toBe("保留我的完整修订草稿");
    if (sourceCurrent) {
      await userEvent.click(
        await screen.findByRole("button", {
          name: "已核对最新版本，保留草稿继续修订",
        }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "提交修订核验" }),
      );
      await screen.findByText(
        "修订已提交独立核验，原有卡片和人工决定继续保留。",
      );
      expect(requests.map((request) => request.expected_version)).toEqual([
        3, 4,
      ]);
      expect(requests[1].card.summary).toBe("保留我的完整修订草稿");
    } else {
      expect(
        screen.queryByRole("button", {
          name: "已核对最新版本，保留草稿继续修订",
        }),
      ).toBeNull();
      expect(
        screen.getByText(
          "原文或研究版本已更新，当前草稿保留，不能提交到旧来源。",
        ),
      ).toBeTruthy();
      expect(requests).toHaveLength(1);
    }
  },
);
test("a full card revision submits edited item and date with its source version and waits for independent verification", async () => {
  let submitted: unknown;
  const draft = {
    id: "c",
    article_id: "article",
    title: "报告",
    summary: "原提要",
    document_type: "报告",
    source_layer: "正文",
    formation_date: {
      text: "年份未详",
      certainty: "unknown",
      normalized: null,
      evidence_ids: [],
    },
    event_date: {
      text: "",
      certainty: "unknown",
      normalized: null,
      evidence_ids: [],
    },
    items: [
      {
        id: "i",
        kind: "evidence",
        title: "拨款申请",
        text: "原有条目",
        evidence_ids: ["e"],
        epistemic_state: "stated",
        attribution: "",
        limitations: [],
      },
    ],
    evidence: [
      {
        id: "e",
        article_id: "article",
        start: 0,
        end: 4,
        quote: "拨款申请",
        footnote_ids: [],
      },
    ],
    mentions: [],
    events: [],
    relations: [],
    identity_links: [],
    tags: [],
    limitations: [],
    alternatives: [],
    questions: [],
    no_argument_reason: null,
    metadata: null,
    coverage: [],
    research_plan: [],
    cross_relations: [],
    question_findings: [],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/revisions")) {
        submitted = JSON.parse(String(init?.body));
        return Response.json({ run_id: "revision", state: "ready" });
      }
      return Response.json({
        card: draft,
        version: 3,
        source_current: true,
        superseded: false,
      });
    }),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <CardEditor cardId="c" onClose={() => {}} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const item = await screen.findByRole("textbox", { name: "条目 1 正文" });
  await userEvent.clear(item);
  await userEvent.type(item, "申请拨款不代表已经拨付");
  const date = screen.getByRole("textbox", { name: "成文时间原文" });
  await userEvent.clear(date);
  await userEvent.type(date, "五月十二日，年份未详");
  await userEvent.click(screen.getByRole("button", { name: "提交修订核验" }));
  await screen.findByText("修订已提交独立核验，原有卡片和人工决定继续保留。");
  expect(submitted).toMatchObject({
    expected_version: 3,
    card: {
      items: [{ text: "申请拨款不代表已经拨付" }],
      formation_date: { text: "五月十二日，年份未详" },
    },
  });
  expect(screen.queryByText("已人工确认")).toBeNull();
});
