// @vitest-environment happy-dom
import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { TopicNavigation } from "./TopicNavigation";

afterEach(() => {
  cleanup();
  sessionStorage.clear();
});

test("topic navigation preserves the explicit topic across all research views", () => {
  const cache = new QueryClient();
  cache.setQueryData(["platform", "runtime"], { workspace_id: "workspace-a" });
  render(
    <MemoryRouter initialEntries={["/timeline?topic=selected-topic"]}>
      <QueryClientProvider client={cache}>
        <TopicNavigation topicId="selected-topic" topicTitle="交通研究" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(screen.getByLabelText("当前专题").textContent).toContain("交通研究");
  expect(screen.getByRole("link", { name: "材料" }).getAttribute("href")).toBe(
    "/studies?topic=selected-topic",
  );
  expect(
    screen.getByRole("link", { name: "研究记录" }).getAttribute("href"),
  ).toBe("/arguments?topic=selected-topic");
  expect(
    screen.getByRole("link", { name: "关系画布" }).getAttribute("href"),
  ).toBe("/studies/selected-topic/canvas");
  expect(
    screen.getByRole("link", { name: "时间线索" }).getAttribute("aria-current"),
  ).toBe("page");
  expect(
    screen.getByRole("link", { name: "AI 分析" }).getAttribute("href"),
  ).toBe("/analysis?topic=selected-topic");
});

test("returning to a topic view restores its selected record and filters only inside the same workspace", async () => {
  const cache = new QueryClient();
  cache.setQueryData(["platform", "runtime"], { workspace_id: "workspace-a" });
  const first = render(
    <MemoryRouter
      initialEntries={[
        "/arguments?topic=topic&kind=note&offset=20&selected=note-a",
      ]}
    >
      <QueryClientProvider client={cache}>
        <TopicNavigation topicId="topic" topicTitle="交通研究" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("link", { name: "时间线索" }));
  expect(
    screen.getByRole("link", { name: "研究记录" }).getAttribute("href"),
  ).toBe("/arguments?topic=topic&kind=note&offset=20&selected=note-a");
  first.unmount();
  const other = new QueryClient();
  other.setQueryData(["platform", "runtime"], { workspace_id: "workspace-b" });
  render(
    <MemoryRouter initialEntries={["/timeline?topic=topic"]}>
      <QueryClientProvider client={other}>
        <TopicNavigation topicId="topic" topicTitle="另一工作区专题" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("link", { name: "研究记录" }).getAttribute("href"),
  ).toBe("/arguments?topic=topic");
});

test("restoration rejects a network URL disguised with a backslash", () => {
  sessionStorage.setItem(
    "hrs:study-view:workspace-a:topic:records",
    "/\\evil.invalid/arguments?topic=topic",
  );
  const cache = new QueryClient();
  cache.setQueryData(["platform", "runtime"], { workspace_id: "workspace-a" });
  render(
    <MemoryRouter initialEntries={["/timeline?topic=topic"]}>
      <QueryClientProvider client={cache}>
        <TopicNavigation topicId="topic" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("link", { name: "研究记录" }).getAttribute("href"),
  ).toBe("/arguments?topic=topic");
});
