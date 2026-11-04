// @vitest-environment happy-dom
import { expect, test, vi, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReviewPanel from "@/features/review/ReviewPanel.tsx";
import { client } from "@/client/api.ts";
import { respondWith } from "@/test/responses.ts";
afterEach(cleanup);
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

test("autosave persists a draft and defer moves on without approving content", async () => {
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
      <ReviewPanel runId="draft-run" />
    </QueryClientProvider>,
  );
  await screen.findByText("原文");
  await userEvent.click(screen.getByRole("button", { name: "编辑正文" }));
  await userEvent.click(screen.getByRole("button", { name: "Markdown 源码" }));
  await userEvent.clear(screen.getByRole("textbox", { name: "Markdown 源码" }));
  await userEvent.type(
    screen.getByRole("textbox", { name: "Markdown 源码" }),
    "修订草稿",
  );
  await waitFor(() => expect(writes).toEqual(["修订草稿"]), { timeout: 2000 });
  await screen.findByText("草稿已保存，尚未确认放行。");
  await userEvent.click(
    screen.getByRole("button", { name: "暂不确定，下一页" }),
  );
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
        <ReviewPanel runId="pending-run" stage={stage} state="processing" />
      </QueryClientProvider>,
    );
    await screen.findByRole("heading", { name: "识别与原件核验进行中" });
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
      <ReviewPanel runId="run" />
    </QueryClientProvider>,
  );
  await screen.findByText("第一页正文");
  await userEvent.click(screen.getByRole("button", { name: "确认本页" }));
  expect(
    screen
      .getByRole("button", { name: /原件第 2 页.*待人工核对/ })
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
      <ReviewPanel runId="run" />
    </QueryClientProvider>,
  );
  await screen.findByText("图1 行动图");
  expect(screen.getByText("本地视觉核验未完成")).toBeDefined();
  expect(screen.getByText(/本项为插图或地图/)).toBeDefined();
  expect(screen.queryByAltText("Image")).toBeNull();
  expect(screen.getByAltText("原书第 54 页").getAttribute("src")).toBe(
    issue.images[0],
  );
});
