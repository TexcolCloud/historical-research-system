// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import EntityLibrary from "./EntityLibrary";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function Location() {
  return <output data-testid="route">{useLocation().search}</output>;
}
test("the entity catalogue filters documents and opens them as entities while retaining pagination", async () => {
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requests.push(url);
      return Response.json({
        items: [{ id: "document-entity", kind: "document", name: "某法令" }],
        total: 31,
        offset: url.includes("offset=30") ? 30 : 0,
        limit: 30,
      });
    }),
  );
  render(
    <MemoryRouter initialEntries={["/research?kind=document&q=法令"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <EntityLibrary />
        <Location />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "某法令 文献" }),
  );
  expect(screen.getByTestId("route").textContent).toContain(
    "entity=document-entity",
  );
  expect(screen.getByTestId("route").textContent).not.toContain("card=");
  expect(screen.getByTestId("route").textContent).toContain("view=network");
  await userEvent.click(screen.getByRole("button", { name: "下一页" }));
  await vi.waitFor(() =>
    expect(requests.some((url) => url.includes("offset=30"))).toBe(true),
  );
  expect(
    requests.every(
      (url) =>
        url.startsWith("/api/v2/research/network/entities?") &&
        url.includes("kind=document"),
    ),
  ).toBe(true);
  expect(screen.queryByText("研究检索")).toBeNull();
  expect(screen.queryByRole("button", { name: "继续查证" })).toBeNull();
});

test("same-name identities expose their own source preview without merging their buttons", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        items: [
          {
            id: "first",
            name: "中央",
            kind: "organization",
            source_title: "第一篇文献",
            card_count: 1,
          },
          {
            id: "second",
            name: "中央",
            kind: "organization",
            source_title: "第二篇文献",
            card_count: 1,
          },
        ],
        total: 2,
        offset: 0,
        limit: 30,
      }),
    ),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <EntityLibrary compact onSelect={vi.fn()} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByText("出处：第一篇文献")).toBeTruthy();
  expect(screen.getByText("出处：第二篇文献")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: /中央/ })).toHaveLength(2);
});
