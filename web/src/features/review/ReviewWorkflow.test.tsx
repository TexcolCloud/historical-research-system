// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReviewPanel from "./ReviewPanel.tsx";
import { client } from "@/client/api.ts";
import { RuntimeScopeContext } from "@/app/runtime-scope.ts";
import { respondWith } from "@/test/responses.ts";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("a pending draft save cannot claim that page judgments are already complete", async () => {
  const issue = {
    id: "saving",
    run_id: "run",
    page: 1,
    pages: [1],
    images: [],
    state: "pending",
    revision: 1,
    text_sha256: "a".repeat(64),
    kind: "page",
    text: "甲字乙",
    reasons: [],
    resolutions: [],
    locations: [
      {
        id: "one",
        page: 1,
        anchor: { exact: "甲字乙", prefix: "", suffix: "" },
        focus: "字",
        boxes: [],
        image_precision: "page",
        message: "核对",
        source_reading: "",
        candidate: "",
        suggestion: "",
      },
    ],
  };
  respondWith((request) =>
    new URL(request.url).pathname === "/api/v2/reviews" ? [issue] : issue,
  );
  let acknowledge!: () => void;
  const saved = new Promise<void>((resolve) => {
    acknowledge = resolve;
  });
  vi.spyOn(client, "PUT").mockImplementation(async () => {
    await saved;
    return {
      data: {
        revision: 2,
        draft_text: issue.text,
        resolutions: [{ id: "one", action: "keep" }],
      },
      response: new Response(),
    };
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RuntimeScopeContext.Provider value="content">
        <ReviewPanel runId="run" />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "识别正确，保留" }),
  );
  expect(screen.queryByRole("region", { name: "本页提交" })).toBeNull();
  // The decision bar exposes submission only after the saved judgments are complete.
  expect(screen.queryByRole("button", { name: "提交本页并继续" })).toBeNull();
  acknowledge();
  await screen.findByRole("region", { name: "本页提交" });
});

test("terminal human review separates judgment, editing and final page submission", async () => {
  const issue = {
    id: "guided",
    run_id: "run",
    page: 1,
    pages: [1],
    images: ["/original.png"],
    state: "pending",
    revision: 1,
    text_sha256: "a".repeat(64),
    kind: "page",
    text: "甲错乙",
    reasons: ["请核对字形"],
    resolutions: [],
    locations: [
      {
        id: "one",
        page: 1,
        anchor: { exact: "甲错乙", prefix: "", suffix: "" },
        focus: "错",
        boxes: [[0.1, 0.2, 0.8, 0.3]],
        image_precision: "line",
        message: "请核对字形",
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
    .mockImplementation(async (_p, options) => ({
      data: {
        revision: 2,
        draft_text: options!.body.text,
        resolutions: options!.body.resolutions,
      },
      response: new Response(),
    }));
  const post = vi.spyOn(client, "POST");
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RuntimeScopeContext.Provider value="content">
        <ReviewPanel runId="run" />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  await screen.findByRole("button", { name: "识别正确，保留" });
  expect(screen.queryByRole("button", { name: /暂不确定/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "提交本页并继续" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "需要修改" }));
  const input = screen.getByRole("textbox", {
    name: "本处最终文字",
  }) as HTMLTextAreaElement;
  expect(input.value).toBe("错");
  await userEvent.clear(input);
  await userEvent.type(input, "修订草稿");
  await userEvent.click(screen.getByRole("button", { name: "取消修改" }));
  expect(screen.queryByRole("textbox", { name: "本处最终文字" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "需要修改" }));
  expect(
    (
      screen.getByRole("textbox", {
        name: "本处最终文字",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe("错");
  await userEvent.click(screen.getByRole("button", { name: "填入机器建议" }));
  expect(
    (
      screen.getByRole("textbox", {
        name: "本处最终文字",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe("对");
  await userEvent.click(
    screen.getByRole("button", { name: /保存修改并(?:继续|检查本页)/ }),
  );
  await screen.findByRole("button", { name: "提交本页并继续" });
  await waitFor(() => expect(put).toHaveBeenCalled());
  expect(put.mock.calls.at(-1)?.[1]?.body.text).toBe("甲对乙");
  expect(post).not.toHaveBeenCalled();
});

test("successive edits keep prior decisions and save both final readings", async () => {
  const location = {
    page: 1,
    boxes: [],
    image_precision: "page",
    message: "待核",
    source_reading: "",
    candidate: "",
  };
  const issue = {
    id: "two",
    run_id: "run",
    page: 1,
    pages: [1],
    images: [],
    state: "pending",
    revision: 1,
    text_sha256: "a".repeat(64),
    kind: "page",
    text: "😀甲错乙，丙错丁。",
    reasons: ["待核"],
    resolutions: [],
    locations: [
      {
        ...location,
        id: "a",
        anchor: { exact: "甲错乙", prefix: "😀", suffix: "，丙错丁。" },
        focus: "错",
        suggestion: "甲对乙",
      },
      {
        ...location,
        id: "b",
        anchor: { exact: "丙错丁", prefix: "😀甲错乙，", suffix: "。" },
        focus: "错",
        suggestion: "丙对丁",
      },
    ],
  };
  respondWith((request) =>
    new URL(request.url).pathname === "/api/v2/reviews" ? [issue] : issue,
  );
  const put = vi
    .spyOn(client, "PUT")
    .mockImplementation(async (_p, options) => ({
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
        <ReviewPanel runId="run" />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  for (let i = 0; i < 2; i++) {
    await userEvent.click(
      await screen.findByRole("button", { name: "需要修改" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "填入机器建议" }));
    await userEvent.click(
      screen.getByRole("button", { name: /保存修改并(?:继续|检查本页)/ }),
    );
  }
  await screen.findByRole("button", { name: "提交本页并继续" });
  expect(put.mock.calls.at(-1)?.[1]?.body.text).toBe("😀甲对乙，丙对丁。");
  expect(put.mock.calls.at(-1)?.[1]?.body.resolutions).toEqual([
    { id: "a", action: "edit" },
    { id: "b", action: "edit" },
  ]);
});
