// @vitest-environment happy-dom
import { expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReviewPanel from "./ReviewPanel";
import { client } from "./client";
import { respondWith } from "./test-responses";

test.each([
  { stage: "ocr", button: "识别完成后可浏览底稿", disabled: true },
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
  await userEvent.click(screen.getByRole("button", { name: "确认无误并放行" }));
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
