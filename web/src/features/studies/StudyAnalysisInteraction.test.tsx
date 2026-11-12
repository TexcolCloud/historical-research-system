// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AnalysisPage } from "./StudyAnalysis";

vi.mock("./StudyTopics", () => ({
  MaterialPreview: ({
    materialId,
    evidenceId,
  }: {
    materialId: string;
    evidenceId: string;
  }) => (
    <aside aria-label="当前建议依据">
      {materialId}:{evidenceId}
    </aside>
  ),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

function fixture() {
  return {
    topic: {
      id: "t1",
      title: "专题",
      materials: [{ id: "m1", title: "材料一", source_current: true }],
      book_ids: [],
      version: 1,
      archived: false,
    },
    analysis: {
      id: "a1",
      topic_id: "t1",
      mode: "summary",
      state: "ready",
      version: 2,
      material_ids: ["m1"],
      prompt: "",
      suggestions: [
        {
          id: "s1",
          title: "第一条建议",
          text: "第一条建议正文",
          limitations: "仍需查阅原文",
          verified: true,
          ignored: false,
          evidence_refs: [{ material_id: "m1", evidence_id: "e1" }],
        },
        {
          id: "s2",
          title: "第二条建议",
          text: "第二条建议正文",
          limitations: "仍需查阅原文",
          verified: true,
          ignored: false,
          evidence_refs: [{ material_id: "m1", evidence_id: "e2" }],
        },
      ],
    },
  };
}
function mountAnalysis(
  onMutation?: (url: string, init: RequestInit) => Response,
  options: {
    initialEntry?: string;
    ignoredFirst?: boolean;
    sourceCurrent?: boolean;
  } = {},
) {
  const { topic, analysis } = fixture();
  analysis.suggestions[0].ignored = options.ignoredFirst ?? false;
  topic.materials[0].source_current = options.sourceCurrent ?? true;
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST" && onMutation) return onMutation(url, init);
      if (url.endsWith("/analyses/a1")) return Response.json(analysis);
      if (url.includes("/analyses?"))
        return Response.json({ items: [analysis], total: 1 });
      if (url.endsWith("/topics/t1")) return Response.json(topic);
      return Response.json({ items: [topic], total: 1 });
    },
  );
  vi.stubGlobal("fetch", fetcher);
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  query.setQueryData(["platform", "runtime"], { workspace_id: "workspace-a" });
  render(
    <MemoryRouter
      initialEntries={[
        options.initialEntry || "/analysis?topic=t1&analysis=a1",
      ]}
    >
      <QueryClientProvider client={query}>
        <AnalysisPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { query, analysis, topic, fetcher };
}

test("ready analysis focuses one suggestion and its evidence while preparation is closed", async () => {
  mountAnalysis();
  await screen.findByText("第一条建议正文");
  expect(screen.queryByRole("combobox", { name: "分析方式" })).toBeNull();
  expect(screen.queryByText("第二条建议正文")).toBeNull();
  expect(
    screen.getByRole("complementary", { name: "当前建议依据" }).textContent,
  ).toBe("m1:e1");
  await userEvent.click(screen.getByRole("button", { name: /第二条建议/ }));
  expect(screen.getByText("第二条建议正文")).toBeTruthy();
  expect(
    screen.getByRole("complementary", { name: "当前建议依据" }).textContent,
  ).toBe("m1:e2");
});

test("personal interpretation survives switching suggestions and adopts separately from the unchanged candidate", async () => {
  const { fetcher, analysis } = mountAnalysis(() =>
    Response.json({ id: "personal-record" }),
  );
  await screen.findByText("第一条建议正文");
  await userEvent.click(screen.getByRole("button", { name: "修改后采纳" }));
  await userEvent.clear(screen.getByRole("textbox", { name: "个人解读正文" }));
  await userEvent.type(
    screen.getByRole("textbox", { name: "个人解读正文" }),
    "我的个人解读，不代替原文",
  );
  await userEvent.click(screen.getByRole("button", { name: /第二条建议/ }));
  await userEvent.click(screen.getByRole("button", { name: /第一条建议/ }));
  expect(
    (
      screen.getByRole("textbox", {
        name: "个人解读正文",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe("我的个人解读，不代替原文");
  await userEvent.click(
    screen.getByRole("button", { name: "采纳此修改为札记" }),
  );
  const link = await screen.findByRole("link", { name: "查看个人记录" });
  expect(link.getAttribute("href")).toContain("record=personal-record");
  const mutation = fetcher.mock.calls.find(
    ([, init]) => init?.method === "POST",
  );
  expect(JSON.parse(String(mutation?.[1]?.body))).toMatchObject({
    expected_version: 2,
    suggestion_id: "s1",
    text: "我的个人解读，不代替原文",
    limitations: "仍需查阅原文",
  });
  expect(analysis.suggestions[0].text).toBe("第一条建议正文");
});

test("ignoring and restoring a suggestion uses versioned server receipts without changing its evidence", async () => {
  let version = 2;
  const { fetcher, analysis } = mountAnalysis((_url, init) => {
    const body = JSON.parse(String(init.body));
    expect(body.expected_version).toBe(version);
    return Response.json({
      ...analysis,
      version: ++version,
      suggestions: analysis.suggestions.map((item) =>
        item.id === "s1" ? { ...item, ignored: body.ignored } : item,
      ),
    });
  });
  await screen.findByText("第一条建议正文");
  await userEvent.click(screen.getByRole("button", { name: "忽略此建议" }));
  expect(await screen.findByText("第二条建议正文")).toBeTruthy();
  expect(screen.queryByText("第一条建议正文")).toBeNull();
  await userEvent.click(
    screen.getByRole("checkbox", { name: "查看已忽略建议 · 1" }),
  );
  await userEvent.click(screen.getByRole("button", { name: /第一条建议/ }));
  expect(screen.getByText("已忽略 · 原建议保留")).toBeTruthy();
  expect(
    (screen.getByRole("button", { name: "采纳为札记" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "恢复建议" }));
  await screen.findByRole("button", { name: "忽略此建议" });
  expect(
    (screen.getByRole("button", { name: "采纳为札记" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
  const posts = fetcher.mock.calls.filter(
    ([, init]) => init?.method === "POST",
  );
  expect(posts).toHaveLength(2);
  expect(
    posts.every(([url]) => String(url).endsWith("/suggestions/s1/disposition")),
  ).toBe(true);
  expect(analysis.suggestions[0].evidence_refs).toEqual([
    { material_id: "m1", evidence_id: "e1" },
  ]);
});

test("a retained preparation cannot submit stale materials and lets the user remove them", async () => {
  const { query } = mountAnalysis();
  await screen.findByText("第一条建议正文");
  await userEvent.click(screen.getByRole("button", { name: "新建分析" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "材料一" }));
  await userEvent.click(screen.getByRole("button", { name: "返回当前分析" }));
  query.setQueryData(["studies", "topic", "t1"], {
    ...fixture().topic,
    materials: [{ id: "m1", title: "材料一", source_current: false }],
  });
  await userEvent.click(screen.getByRole("button", { name: "新建分析" }));
  expect(
    (screen.getByRole("button", { name: "分析所选材料" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  const stale = screen.getByRole("checkbox", {
    name: /材料一/,
  }) as HTMLInputElement;
  expect(stale.checked).toBe(true);
  expect(stale.disabled).toBe(false);
  await userEvent.click(stale);
  expect(stale.checked).toBe(false);
});

test("a version conflict can reload the analysis in place without losing the personal interpretation", async () => {
  let attempts = 0;
  const { analysis, fetcher } = mountAnalysis(() =>
    ++attempts === 1
      ? Response.json(
          { detail: "分析已有新版本，请重新读取后采纳。" },
          { status: 409 },
        )
      : Response.json({ id: "personal-record" }),
  );
  await screen.findByText("第一条建议正文");
  await userEvent.click(screen.getByRole("button", { name: "修改后采纳" }));
  await userEvent.clear(screen.getByRole("textbox", { name: "个人解读正文" }));
  await userEvent.type(
    screen.getByRole("textbox", { name: "个人解读正文" }),
    "版本冲突后保留的个人草稿",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "采纳此修改为札记" }),
  );
  await screen.findByRole("alert");
  analysis.version = 3;
  analysis.suggestions.reverse();
  await userEvent.click(screen.getByRole("button", { name: "重新读取分析" }));
  await screen.findByText("已重新读取分析，个人解读草稿仍保留。");
  expect(
    (
      screen.getByRole("textbox", {
        name: "个人解读正文",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe("版本冲突后保留的个人草稿");
  expect(screen.getByRole("heading", { name: "第一条建议" })).toBeTruthy();
  await userEvent.click(
    screen.getByRole("button", { name: "采纳此修改为札记" }),
  );
  await screen.findByRole("link", { name: "查看个人记录" });
  const posts = fetcher.mock.calls.filter(
    ([, init]) => init?.method === "POST",
  );
  expect(
    posts.map(([, init]) => JSON.parse(String(init?.body)).expected_version),
  ).toEqual([2, 3]);
});

test("returning to an ignored suggestion restores that exact suggestion and evidence", async () => {
  mountAnalysis(undefined, {
    ignoredFirst: true,
    initialEntry:
      "/analysis?topic=t1&analysis=a1&suggestion=s1&proof_material=m1&evidence=e1",
  });
  await screen.findByText("第一条建议正文");
  expect(screen.queryByText("第二条建议正文")).toBeNull();
  expect(
    (
      screen.getByRole("checkbox", {
        name: "查看已忽略建议 · 1",
      }) as HTMLInputElement
    ).checked,
  ).toBe(true);
  expect(
    screen.getByRole("complementary", { name: "当前建议依据" }).textContent,
  ).toBe("m1:e1");
  await userEvent.click(
    screen.getByRole("checkbox", { name: "查看已忽略建议 · 1" }),
  );
  expect(screen.getByText("第二条建议正文")).toBeTruthy();
});

test("verified suggestions with changed topic sources remain readable but cannot be adopted", async () => {
  const { fetcher } = mountAnalysis(undefined, { sourceCurrent: false });
  await screen.findByText("第一条建议正文");
  expect(
    screen.getByText(
      "所引来源已修订或已移出当前专题，旧建议与引用保留；请重新选材分析。",
    ),
  ).toBeTruthy();
  expect(
    (screen.getByRole("button", { name: "采纳为札记" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(
    (screen.getByRole("button", { name: "修改后采纳" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(screen.getByText("AI 建议 · 保留版引用已核对")).toBeTruthy();
  expect(
    fetcher.mock.calls.filter(([, init]) => init?.method === "POST"),
  ).toHaveLength(0);
});

test("an exact evidence link never substitutes a different source from the selected suggestion", async () => {
  mountAnalysis(undefined, {
    initialEntry:
      "/analysis?topic=t1&analysis=a1&suggestion=s1&proof_material=m1&evidence=other",
  });
  await screen.findByText("第一条建议正文");
  expect(
    screen.queryByRole("complementary", { name: "当前建议依据" }),
  ).toBeNull();
  expect(
    screen.getByText("指定的原文依据不属于当前建议，请重新选择依据。"),
  ).toBeTruthy();
  await userEvent.click(
    screen.getByRole("button", { name: "查看原文依据 1 · e1" }),
  );
  expect(
    screen.getByRole("complementary", { name: "当前建议依据" }).textContent,
  ).toBe("m1:e1");
});
