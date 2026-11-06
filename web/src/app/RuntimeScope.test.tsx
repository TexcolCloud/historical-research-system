// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AppShell from "./AppShell.tsx";
import ReviewPanel from "@/features/review/ReviewPanel.tsx";
import { client } from "@/client/api.ts";

vi.mock("@/hooks/useWorkspaceEvents.ts", () => ({
  useWorkspaceEvents: () => true,
}));
afterEach(cleanup);

test("an unknown runtime keeps draft decisions but blocks final approval until scope is reloaded", async () => {
  let available = false;
  let draftWrites = 0;
  let finalWrites = 0;
  const issue = {
    id: "scope-page",
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
        message: "核对这个字",
        boxes: [],
        image_precision: "page",
        candidate: "字",
        source_reading: "字",
        suggestion: "",
      },
    ],
  };
  const middleware = {
    onRequest: async ({ request }: { request: Request }) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/v2/runtime")
        return available
          ? Response.json({ validation_scope: "workflow_only" })
          : Response.json({ detail: "unavailable" }, { status: 503 });
      if (path.endsWith("/draft")) {
        draftWrites += 1;
        const body = await request.json();
        return Response.json({
          revision: 2,
          draft_text: body.text,
          resolutions: body.resolutions,
        });
      }
      if (path.endsWith("/decisions")) {
        finalWrites += 1;
        return Response.json({
          issue_id: issue.id,
          run_id: "run",
          revision: 3,
          pending_count: 0,
          next_issue_id: null,
        });
      }
      return Response.json(path === "/api/v2/reviews" ? [issue] : issue);
    },
  };
  client.use(middleware);
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  try {
    render(
      <QueryClientProvider client={query}>
        <MemoryRouter initialEntries={["/review"]}>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="review" element={<ReviewPanel runId="run" />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "识别正确，保留" }),
    );
    await screen.findByRole("region", { name: "本页提交" });
    const submit = screen.getByRole("button", {
      name: "确认工作区范围后再提交",
    });
    expect(submit.hasAttribute("disabled")).toBe(true);
    await waitFor(() => expect(draftWrites).toBe(1));
    expect(finalWrites).toBe(0);
    expect(screen.getByRole("alert").textContent).toContain("工作区范围未确认");
    available = true;
    await userEvent.click(
      screen.getByRole("button", { name: "重新确认工作区" }),
    );
    const confirmed = await screen.findByRole("button", {
      name: "按授权提交流程测试结果",
    });
    expect(confirmed.hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("note").textContent).toContain(
      "不代表内容准确率通过",
    );
  } finally {
    client.eject(middleware);
    query.clear();
  }
});
