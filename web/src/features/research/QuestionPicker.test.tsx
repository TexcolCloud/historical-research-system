// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import QuestionPicker from "./QuestionPicker";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
test.each(["source", "card", "entity", "relation"] as const)(
  "%s selections submit only the retained target and CAS version, leaving source evidence to the server",
  async (kind) => {
    const question = {
      id: "q",
      title: "经费是否执行",
      version: 7,
      status: "open",
      book_ids: [],
      notes: "",
      selections: [],
      findings: [],
      runs: [],
    };
    let body: Record<string, unknown> | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") {
          body = JSON.parse(String(init.body));
          return Response.json({ ...question, version: 8 });
        }
        return Response.json({
          items: [question],
          total: 1,
          offset: 0,
          limit: 100,
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
          <QuestionPicker
            material={{
              kind,
              target_id: "retained-target",
              title: "显示标题",
              ...(kind === "source"
                ? { start: 2, end: 8, text_sha256: "a".repeat(64) }
                : {}),
            }}
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "保存到研究问题" }),
    );
    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: "选材所属问题" }),
      "q",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "此次选材说明" }),
      "需要继续核对",
    );
    await userEvent.click(screen.getByRole("button", { name: "保存选材" }));
    await screen.findByText("选材已保存。");
    expect(body).toMatchObject({
      kind,
      target_id: "retained-target",
      expected_version: 7,
      decision: "saved",
      note: "需要继续核对",
    });
    expect(body).not.toHaveProperty("quote");
    expect(body).not.toHaveProperty("evidence");
    expect(body).not.toHaveProperty("title");
    if (kind === "source")
      expect(body).toMatchObject({
        start: 2,
        end: 8,
        text_sha256: "a".repeat(64),
      });
    expect(
      (screen.getByRole("button", { name: "保存选材" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  },
);
