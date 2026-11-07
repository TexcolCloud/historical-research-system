// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";
import type { components } from "@/client/schema.d.ts";
import { client } from "@/client/api.ts";
import { queryKeys } from "@/client/query-keys.ts";
import { useReviewSession } from "./useReviewSession.ts";
import {
  applyReviewReceipt,
  issuesKey,
  mergeReviewIssue,
} from "./review-cache.ts";
import IssueEditor from "./IssueEditor.tsx";
import ReviewPanel from "./ReviewPanel.tsx";
import { RuntimeScopeContext } from "@/app/runtime-scope.ts";
import { respondWith } from "@/test/responses.ts";

type Issue = components["schemas"]["ReviewIssue"];
const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((query) => query.clear());
  vi.restoreAllMocks();
});

function issue(): Issue {
  return {
    id: "page",
    run_id: "run",
    page: 1,
    pages: [1],
    images: [],
    state: "pending",
    revision: 1,
    text_sha256: "a".repeat(64),
    review_input_sha256: "b".repeat(64),
    draft_conflict: false,
    text: "甲错乙，丙错丁，戊错己。",
    kind: "page",
    reasons: [],
    resolutions: [],
    locations: ["甲错乙", "丙错丁", "戊错己"].map((exact, index) => ({
      id: String(index + 1),
      page: 1,
      anchor: { exact, prefix: "", suffix: "" },
      focus: "错",
      boxes: [],
      image_precision: "page",
      message: "核对",
      source_reading: "",
      candidate: exact,
      suggestion: "",
      text_sha256: "",
      image_sha256: "",
      text_status: "located",
      text_precision: "character",
      offset_unit: "unicode_codepoint",
      location: "",
      unlocated_reason: "",
    })),
  };
}
function session(initial = issue()) {
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(query);
  return {
    query,
    ...renderHook(({ value }) => useReviewSession(value, vi.fn()), {
      initialProps: { value: initial },
      wrapper: ({ children }: PropsWithChildren) => (
        <QueryClientProvider client={query}>{children}</QueryClientProvider>
      ),
    }),
  };
}

test("a newer same-input server draft reconciles judgments and the next write revision", async () => {
  const original = issue();
  const view = session(original);
  const latest: Issue = {
    ...original,
    revision: 7,
    draft_text: original.text,
    resolutions: [
      { id: "1", action: "keep" },
      { id: "2", action: "keep" },
    ],
  };
  view.rerender({ value: latest });
  await waitFor(() => expect(view.result.current.completed).toBe(2));
  expect(view.result.current.current?.id).toBe("3");
  const put = vi
    .spyOn(client, "PUT")
    .mockImplementation(async (_path, options) => ({
      data: {
        revision: 8,
        draft_text: options!.body.text,
        resolutions: options!.body.resolutions,
      },
      response: new Response(),
    }));
  await act(async () => {
    await view.result.current.finish("keep");
  });
  expect(put.mock.calls[0][1]?.body.expected_revision).toBe(7);
  expect(view.result.current.ready).toBe(true);
  expect(view.result.current.current).toBeUndefined();
});

test("a debounce queued during a slow manual save cannot revoke its completed judgment", async () => {
  const view = session();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const server: { revision: number; resolutions: unknown } = {
    revision: 1,
    resolutions: [],
  };
  const put = vi
    .spyOn(client, "PUT")
    .mockImplementation(async (_path, options) => {
      if (server.revision === 1) await gate;
      expect(options!.body.expected_revision).toBe(server.revision);
      server.resolutions = options!.body.resolutions;
      return {
        data: {
          revision: ++server.revision,
          draft_text: options!.body.text,
          resolutions: options!.body.resolutions,
        },
        response: new Response(),
      };
    });
  act(() => {
    view.result.current.beginEdit();
  });
  act(() => {
    view.result.current.changeFocus("对");
  });
  let finishing!: Promise<void>;
  act(() => {
    finishing = view.result.current.finish("edit");
  });
  await waitFor(() => expect(put).toHaveBeenCalledOnce());
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 850));
  });
  await act(async () => {
    release();
    await finishing;
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
  expect(server.resolutions).toEqual([{ id: "1", action: "edit" }]);
  expect(put).toHaveBeenCalledOnce();
  expect(view.result.current.current?.id).toBe("2");
});

test("submission cancels old list and detail reads without changing the issue revision", async () => {
  const query = new QueryClient();
  clients.push(query);
  const original = issue();
  query.setQueryData(issuesKey("run"), [original]);
  query.setQueryData(queryKeys.issue(original.id), {
    ...original,
    revision: 4,
  });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const oldList = query
    .fetchQuery({
      queryKey: issuesKey("run"),
      queryFn: async () => {
        await gate;
        return [original];
      },
    })
    .catch(() => undefined);
  const oldDetail = query
    .fetchQuery({
      queryKey: queryKeys.issue(original.id),
      queryFn: async () => {
        await gate;
        return original;
      },
    })
    .catch(() => undefined);
  await applyReviewReceipt(query, {
    decision_id: "decision",
    issue_id: original.id,
    run_id: "run",
    revision: 99,
    pending_count: 0,
    next_issue_id: null,
    state: "awaiting_review",
  });
  release();
  await Promise.all([oldList, oldDetail]);
  expect(query.getQueryData(issuesKey("run"))).toEqual([]);
  expect(query.getQueryData(queryKeys.issue(original.id))).toMatchObject({
    state: "approved",
    revision: 4,
  });
});

test("manual continuation waits for an in-flight autosave and uses its acknowledged revision", async () => {
  const view = session();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let revision = 1;
  const put = vi
    .spyOn(client, "PUT")
    .mockImplementation(async (_path, options) => {
      if (revision === 1) await gate;
      expect(options!.body.expected_revision).toBe(revision);
      return {
        data: {
          revision: ++revision,
          draft_text: options!.body.text,
          resolutions: options!.body.resolutions,
        },
        response: new Response(),
      };
    });
  act(() => {
    view.result.current.beginEdit();
  });
  act(() => {
    view.result.current.changeFocus("对");
  });
  await waitFor(() => expect(put).toHaveBeenCalledOnce(), { timeout: 1500 });
  let finishing!: Promise<void>;
  act(() => {
    finishing = view.result.current.finish("edit");
  });
  await act(async () => {
    release();
    await finishing;
  });
  expect(put).toHaveBeenCalledTimes(2);
  expect(put.mock.calls[1][1]?.body.resolutions).toEqual([
    { id: "1", action: "edit" },
  ]);
  expect(view.result.current.current?.id).toBe("2");
});

test("an external revision preserves dirty input until the user chooses its version", async () => {
  const original = issue();
  const view = session(original);
  act(() => {
    view.result.current.beginEdit();
  });
  act(() => {
    view.result.current.changeFocus("本地修订");
  });
  const local = view.result.current.text;
  view.rerender({
    value: { ...original, revision: 9, draft_text: "另一窗口的草稿" },
  });
  await waitFor(() =>
    expect(view.result.current.remoteDraft?.revision).toBe(9),
  );
  expect(view.result.current.text).toBe(local);
  const put = vi
    .spyOn(client, "PUT")
    .mockImplementation(async (_path, options) => ({
      data: {
        revision: 10,
        draft_text: options!.body.text,
        resolutions: options!.body.resolutions,
      },
      response: new Response(),
    }));
  act(() => {
    view.result.current.resolveRemote(true);
  });
  await act(async () => {
    await view.result.current.finish("edit");
  });
  expect(put.mock.calls[0][1]?.body.expected_revision).toBe(9);
  expect(put.mock.calls[0][1]?.body.text).toBe(local);
  expect(view.result.current.current?.id).toBe("2");
});

test("two immediate finish calls create one judgment and advance once", async () => {
  const view = session();
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
  await act(async () => {
    await Promise.all([
      view.result.current.finish("keep"),
      view.result.current.finish("keep"),
    ]);
  });
  expect(put).toHaveBeenCalledOnce();
  expect(view.result.current.completed).toBe(1);
  expect(view.result.current.current?.id).toBe("2");
});

test("a queued save skipped for a terminal cached page never completes a local judgment", async () => {
  const original = issue();
  const view = session(original);
  view.query.setQueryData(queryKeys.issue(original.id), {
    ...original,
    state: "approved",
  });
  const put = vi.spyOn(client, "PUT");
  await act(async () => {
    await expect(view.result.current.finish("keep")).rejects.toThrow(
      "本页已经提交",
    );
  });
  expect(put).not.toHaveBeenCalled();
  expect(view.result.current.completed).toBe(0);
});

test("a save conflict re-reads an already submitted page and blocks further writes", async () => {
  const original = issue();
  const view = session(original);
  const put = vi.spyOn(client, "PUT").mockResolvedValue({
    response: new Response(null, { status: 409 }),
    error: { detail: [] },
  });
  respondWith(() => ({ ...original, revision: 3, state: "approved" }));
  await act(async () => {
    await expect(view.result.current.finish("keep")).rejects.toThrow(
      "本页已经提交",
    );
  });
  expect(view.result.current.readOnly).toBe(true);
  await act(async () => {
    await view.result.current.finish("keep");
  });
  expect(put).toHaveBeenCalledOnce();
  expect(view.query.getQueryData(queryKeys.issue(original.id))).toMatchObject({
    state: "approved",
  });
});

test("a response from an older input cannot close a freshly reopened review", async () => {
  const query = new QueryClient();
  clients.push(query);
  const original = issue();
  const reopened = {
    ...original,
    revision: 9,
    review_input_sha256: "c".repeat(64),
  };
  query.setQueryData(issuesKey("run"), [reopened]);
  query.setQueryData(queryKeys.issue(original.id), reopened);
  const applied = await applyReviewReceipt(
    query,
    {
      decision_id: "old",
      issue_id: original.id,
      run_id: "run",
      revision: 4,
      pending_count: 0,
      next_issue_id: null,
      state: "awaiting_review",
    },
    original,
  );
  expect(applied).toBe(false);
  expect(query.getQueryData(issuesKey("run"))).toEqual([reopened]);
  expect(query.getQueryData(queryKeys.issue(original.id))).toEqual(reopened);
});

test("a newer external draft arriving during a save pauses the old editor instead of advancing", async () => {
  const original = issue();
  const view = session(original);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const put = vi
    .spyOn(client, "PUT")
    .mockImplementation(async (_path, options) => {
      await gate;
      return {
        data: {
          revision: 2,
          draft_text: options!.body.text,
          resolutions: options!.body.resolutions,
        },
        response: new Response(),
      };
    });
  act(() => {
    view.result.current.beginEdit();
  });
  act(() => {
    view.result.current.changeFocus("本地修订");
  });
  let finishing!: Promise<unknown>;
  act(() => {
    finishing = view.result.current.finish("edit");
  });
  const outcome = finishing.catch((error: unknown) => error);
  await waitFor(() => expect(put).toHaveBeenCalledOnce());
  view.rerender({
    value: { ...original, revision: 9, draft_text: "后续服务器草稿" },
  });
  await waitFor(() =>
    expect(view.result.current.remoteDraft?.revision).toBe(9),
  );
  await act(async () => {
    release();
    expect((await outcome) instanceof Error).toBe(true);
  });
  expect(view.result.current.current?.id).toBe("1");
  expect(view.result.current.editing).toBe(true);
  expect(view.result.current.text).toContain("本地修订");
});

test("the last saved doubt clears its focus and moves keyboard focus to page submission", async () => {
  const original = issue();
  const query = new QueryClient();
  clients.push(query);
  let revision = 1;
  vi.spyOn(client, "PUT").mockImplementation(async (_path, options) => ({
    data: {
      revision: ++revision,
      draft_text: options!.body.text,
      resolutions: options!.body.resolutions,
    },
    response: new Response(),
  }));
  render(
    <QueryClientProvider client={query}>
      <IssueEditor issue={original} onSaved={vi.fn()} workflowTest={false} />
    </QueryClientProvider>,
  );
  for (let index = 0; index < 3; index++)
    await userEvent.click(
      await screen.findByRole("button", { name: "识别正确，保留" }),
    );
  await screen.findByRole("region", { name: "本页提交" });
  expect(screen.queryByRole("region", { name: "当前疑点" })).toBeNull();
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "提交本页并继续" }),
  );
});

test.each([
  { label: "newer draft", revision: 4, state: "pending", input: "b" },
  {
    label: "already approved page",
    revision: 2,
    state: "approved",
    input: "b",
  },
  { label: "reopened input", revision: 5, state: "pending", input: "c" },
])(
  "a late conflict GET cannot roll back a $label",
  async ({ revision, state, input }) => {
    const original = issue(),
      view = session(original);
    vi.spyOn(client, "PUT").mockResolvedValue({
      response: new Response(null, { status: 409 }),
      error: { detail: [] },
    });
    let release!: () => void,
      requested = false;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const older = {
      ...original,
      revision: 2,
      draft_text: "旧草稿",
      state: input === "c" ? "approved" : "pending",
    };
    const latest: Issue = {
      ...original,
      revision,
      state,
      review_input_sha256: input.repeat(64),
      draft_text: "最新草稿",
    };
    const middleware = {
      onRequest: async () => {
        requested = true;
        await gate;
        return Response.json(older);
      },
    };
    client.use(middleware);
    try {
      let outcome!: Promise<unknown>;
      act(() => {
        outcome = view.result.current
          .finish("keep")
          .catch((error: unknown) => error);
      });
      await waitFor(() => expect(requested).toBe(true));
      act(() => {
        view.query.setQueryData(queryKeys.issue(original.id), latest);
        view.rerender({ value: latest });
      });
      await act(async () => {
        release();
        await outcome;
      });
      expect(view.query.getQueryData(queryKeys.issue(original.id))).toEqual(
        latest,
      );
      if (state === "approved") {
        expect(view.result.current.readOnly).toBe(true);
        expect(view.result.current.remoteDraft).toBeNull();
      } else {
        expect(view.result.current.readOnly).toBe(false);
        expect(view.result.current.remoteDraft?.revision).toBe(latest.revision);
        expect(view.result.current.remoteDraft?.review_input_sha256).toBe(
          latest.review_input_sha256,
        );
      }
    } finally {
      client.eject(middleware);
    }
  },
);

test("an ordinary detail GET cannot replace a newer input already displayed by the review panel", async () => {
  const original = issue();
  const latest: Issue = {
    ...original,
    revision: 5,
    review_input_sha256: "c".repeat(64),
  };
  let release!: () => void,
    requested = false;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const middleware = {
    onRequest: async ({ request }: { request: Request }) => {
      if (new URL(request.url).pathname === "/api/v2/reviews")
        return Response.json([original]);
      requested = true;
      await gate;
      return Response.json(original);
    },
  };
  client.use(middleware);
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(query);
  try {
    render(
      <QueryClientProvider client={query}>
        <RuntimeScopeContext.Provider value="content">
          <ReviewPanel runId="run" />
        </RuntimeScopeContext.Provider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(requested).toBe(true));
    act(() => {
      query.setQueryData(queryKeys.issue(original.id), latest);
    });
    await act(async () => {
      release();
    });
    await waitFor(() =>
      expect(
        query.getQueryState(queryKeys.issue(original.id))?.fetchStatus,
      ).toBe("idle"),
    );
    expect(query.getQueryData(queryKeys.issue(original.id))).toEqual(latest);
  } finally {
    client.eject(middleware);
  }
});

test("a genuinely newer input can reopen a completed review without a permanent approval tombstone", () => {
  const approved = { ...issue(), state: "approved", revision: 3 };
  const reopened = {
    ...approved,
    state: "pending",
    revision: 4,
    review_input_sha256: "c".repeat(64),
  };
  expect(mergeReviewIssue(approved, reopened)).toEqual(reopened);
});
