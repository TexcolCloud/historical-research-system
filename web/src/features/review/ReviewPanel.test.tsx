// @vitest-environment happy-dom
import { expect, test, vi, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReviewPanel from "@/features/review/ReviewPanel.tsx";
import { client } from "@/client/api.ts";
import { RuntimeScopeContext } from "@/app/runtime-scope.ts";
import { respondWith } from "@/test/responses.ts";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
test("a native handoff selects its physical page and keeps the desktop queue keyboard accessible", async () => {
  const issues = [1, 7].map((page) => ({
    id: `issue-${page}`,
    run_id: "run",
    page,
    revision: 1,
    state: "pending",
    text_sha256: "d".repeat(64),
    text: `候选页 ${page}`,
    images: [],
    pages: [page],
    kind: "page",
    reasons: [],
    locations: [],
    resolutions: [],
  }));
  respondWith((request) => {
    const path = new URL(request.url).pathname;
    return path === "/api/v2/reviews"
      ? issues
      : issues.find((issue) => path.endsWith(issue.id));
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RuntimeScopeContext.Provider value="content">
        <ReviewPanel runId="run" requestedPage={7} />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("候选页 7")).toBeDefined();
  expect(
    screen
      .getByRole("button", { name: "原件第 7 页" })
      .getAttribute("aria-current"),
  ).toBe("true");
  const user = userEvent.setup();
  expect(screen.getByRole("navigation", { name: "问题清单" })).toBeDefined();
  expect(screen.queryByRole("button", { name: "待核对页面 · 2" })).toBeNull();
  const page = screen.getByRole("button", { name: "原件第 1 页" });
  page.focus();
  await user.keyboard("{Enter}");
  await screen.findByText("候选页 1");
  expect(page.getAttribute("aria-current")).toBe("true");
  expect(document.activeElement).toBe(page);
});
test("located doubts highlight text, show source region, and require an explicit per-doubt decision", async () => {
  const location = {
    id: "doubt-1",
    page: 1,
    anchor: { exact: "石待辨渡口", prefix: "", suffix: "" },
    focus: "待辨",
    boxes: [[0.2, 0.3, 0.4, 0.5]],
    image_precision: "region",
    message: "字形待核",
    source_reading: "石岔渡口",
    candidate: "石待辨渡口",
    suggestion: "岔",
  };
  const issue = {
    id: "located",
    run_id: "located-run",
    page: 1,
    revision: 1,
    state: "pending",
    text_sha256: "d".repeat(64),
    text: "石待辨渡口",
    images: ["/source.png"],
    pages: [1],
    kind: "page",
    reasons: [{ explanation: "字形待核" }],
    locations: [location],
    resolutions: [],
  };
  respondWith((request) =>
    new URL(request.url).pathname === "/api/v2/reviews" ? [issue] : issue,
  );
  const put = vi
    .spyOn(client, "PUT")
    .mockImplementation(async (_path, options) => ({
      data: {
        revision: 2,
        draft_text: options!.body.text,
        resolutions: options!.body.resolutions,
      },
      response: new Response(),
    }));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RuntimeScopeContext.Provider value="content">
        <ReviewPanel runId="located-run" />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  await screen.findByText("疑点 1 / 1");
  expect(
    document.querySelector('mark[data-review-id="doubt-1"]')?.textContent,
  ).toBe("待辨");
  expect(
    screen
      .getByLabelText("原图疑点框")
      .querySelector("rect")
      ?.getAttribute("x"),
  ).toBe("0.2");
  expect(
    screen
      .getByRole("button", { name: "提交本页并继续" })
      .hasAttribute("disabled"),
  ).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "识别正确，保留" }));
  await waitFor(() => expect(put).toHaveBeenCalled());
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "提交本页并继续" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  expect(put.mock.calls[0][1]?.body.resolutions).toEqual([
    { id: "doubt-1", action: "keep" },
  ]);
  put.mockRestore();
});
vi.mock("@/editor/LazyMarkdownEditor.tsx", () => ({
  MarkdownEditor: ({
    value,
    onChange,
  }: {
    value: string;
    onChange: (text: string) => void;
  }) => (
    <textarea
      aria-label="正文编辑"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

test("adopting a full-anchor proposal replaces only the focused error without duplicating context", async () => {
  const issue = {
    id: "scope",
    run_id: "scope-run",
    page: 1,
    revision: 1,
    state: "pending",
    text_sha256: "a".repeat(64),
    text: "开头甲错乙结尾",
    images: [],
    pages: [1],
    kind: "page",
    reasons: ["待核"],
    resolutions: [],
    locations: [
      {
        id: "scope-doubt",
        page: 1,
        anchor: { exact: "甲错乙", prefix: "开头", suffix: "结尾" },
        focus: "错",
        boxes: [],
        image_precision: "page",
        message: "待核",
        candidate: "甲错乙",
        source_reading: "甲对乙",
        suggestion: "甲对乙",
      },
    ],
  };
  respondWith((request) =>
    new URL(request.url).pathname === "/api/v2/reviews" ? [issue] : issue,
  );
  const put = vi
    .spyOn(client, "PUT")
    .mockImplementation(async (_path, options) => ({
      data: {
        revision: 2,
        draft_text: options!.body.text,
        resolutions: options!.body.resolutions,
      },
      response: new Response(),
    }));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RuntimeScopeContext.Provider value="content">
        <ReviewPanel runId="scope-run" />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  await screen.findByText("疑点 1 / 1");
  await userEvent.click(screen.getByRole("button", { name: "需要修改" }));
  await userEvent.click(screen.getByRole("button", { name: "填入机器建议" }));
  expect(
    (
      screen.getByRole("textbox", {
        name: "本处最终文字",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe("对");
  await userEvent.click(screen.getByRole("button", { name: "保存修改并继续" }));
  await waitFor(() => expect(put).toHaveBeenCalled());
  put.mockRestore();
});

test("autosave preserves unfinished work across page navigation without submitting a decision", async () => {
  const first = {
    id: "draft-one",
    run_id: "draft-run",
    page: 1,
    revision: 1,
    state: "pending",
    text_sha256: "c".repeat(64),
    text: "原文",
    images: [],
    pages: [1],
    kind: "page",
    reasons: ["待核对"],
  };
  const second = { ...first, id: "draft-two", page: 2, text: "下一页" };
  respondWith((request) =>
    new URL(request.url).pathname === "/api/v2/reviews"
      ? [first, second]
      : request.url.endsWith("/draft-one")
        ? first
        : second,
  );
  const writes: string[] = [];
  const put = vi
    .spyOn(client, "PUT")
    .mockImplementation(async (_path, options) => {
      writes.push(options!.body.text);
      return {
        data: { revision: 2, draft_text: options!.body.text },
        response: new Response(),
      };
    });
  const post = vi.spyOn(client, "POST");
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RuntimeScopeContext.Provider value="content">
        <ReviewPanel runId="draft-run" />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  await screen.findByText("原文");
  await userEvent.click(screen.getByRole("button", { name: "需要修改" }));
  await userEvent.click(
    screen.getByRole("button", { name: "切换 Markdown 源码" }),
  );
  await userEvent.clear(screen.getByRole("textbox", { name: "Markdown 源码" }));
  await userEvent.type(
    screen.getByRole("textbox", { name: "Markdown 源码" }),
    "修订草稿",
  );
  await waitFor(() => expect(writes).toEqual(["修订草稿"]), { timeout: 2000 });
  await screen.findByText("草稿已自动保存 · 尚未提交本页");
  await userEvent.click(screen.getByRole("button", { name: "原件第 2 页" }));
  await screen.findByText("下一页");
  expect(post).not.toHaveBeenCalled();
  put.mockRestore();
  post.mockRestore();
});

test.each([
  { stage: "ocr", button: "浏览全部识别底稿", disabled: false },
  { stage: "vision", button: "浏览全部识别底稿", disabled: false },
])(
  "unfinished $stage is not presented as a cleared human-review list",
  async ({ stage, button, disabled }) => {
    respondWith(() => []);
    const query = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={query}>
        <RuntimeScopeContext.Provider value="content">
          <ReviewPanel runId="pending-run" stage={stage} state="processing" />
        </RuntimeScopeContext.Provider>
      </QueryClientProvider>,
    );
    await screen.findByRole("heading", { name: "文字提取与原图核对进行中" });
    expect(
      screen.queryByRole("heading", { name: "当前没有待核对内容" }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: button }).hasAttribute("disabled"),
    ).toBe(disabled);
  },
);

test("confirmation uses the returned next issue immediately without a whole-page reload", async () => {
  const first = {
    id: "one",
    run_id: "run",
    page: 1,
    revision: 1,
    state: "pending",
    text_sha256: "a".repeat(64),
  };
  const next = { ...first, id: "two", page: 2 };
  respondWith((request) => {
    const path = new URL(request.url).pathname;
    return path === "/api/v2/reviews"
      ? [first, next]
      : {
          ...(path.endsWith("/one") ? first : next),
          text: path.endsWith("/one") ? "第一页正文" : "第二页正文",
          images: [],
          pages: [1],
          kind: "table",
          reasons: ["请核对"],
        };
  });
  let complete!: () => void;
  const pending = new Promise<void>((resolve) => {
    complete = resolve;
  });
  vi.spyOn(client, "POST").mockImplementation(async () => {
    await pending;
    return {
      data: {
        decision_id: "saved",
        issue_id: "one",
        run_id: "run",
        revision: 2,
        pending_count: 1,
        next_issue_id: "two",
        state: "awaiting_review",
      },
      response: new Response(),
    };
  });
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={query}>
      <RuntimeScopeContext.Provider value="content">
        <ReviewPanel runId="run" />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  await screen.findByText("第一页正文");
  await userEvent.click(screen.getByRole("button", { name: "识别正确，保留" }));
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "提交本页并继续" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  await userEvent.click(screen.getByRole("button", { name: "提交本页并继续" }));
  expect(
    screen
      .getByRole("button", { name: "原件第 2 页" })
      .hasAttribute("disabled"),
  ).toBe(true);
  complete();
  await waitFor(() => expect(screen.queryByText("第二页正文")).not.toBeNull());
  expect(screen.queryByText("第一页正文")).toBeNull();
  vi.restoreAllMocks();
});

test("map review explains the image carrier without rendering local file URLs as broken images", async () => {
  const issue = {
    id: "map",
    run_id: "run",
    page: 54,
    pages: [54],
    revision: 1,
    state: "pending",
    text_sha256: "b".repeat(64),
    kind: "figure",
    text: "Image\n\n![Image](D:/private cache/map.png)\n\n图1 行动图",
    images: ["/api/v2/runs/run/artifacts/pages/54.png"],
    reasons: [
      "deepseek-review-incomplete",
      "independent-source-reading-failed",
    ],
  };
  respondWith((request) =>
    new URL(request.url).pathname === "/api/v2/reviews" ? [issue] : issue,
  );
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={query}>
      <RuntimeScopeContext.Provider value="content">
        <ReviewPanel runId="run" />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  await screen.findByText("图1 行动图");
  expect(screen.getByText("原图核对未完成")).toBeDefined();
  expect(screen.getByText(/本项为插图或地图/)).toBeDefined();
  expect(screen.queryByAltText("Image")).toBeNull();
  expect(screen.getByAltText("原书第 54 页").getAttribute("src")).toBe(
    issue.images[0],
  );
});
