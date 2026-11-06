// @vitest-environment happy-dom
import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import NavigationSearch from "./NavigationSearch.tsx";

afterEach(cleanup);
test("page lookup distinguishes invalid page order from pages not yet approved", async () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NavigationSearch
        title="标题"
        pages={[{ page: 2, headings: [] }]}
        pageCount={9}
        runId="run"
        busy={false}
        onChoose={() => undefined}
      />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByText("按 PDF 页序定位"));
  const input = screen.getByRole("spinbutton", { name: "PDF 页序" });
  await userEvent.type(input, "10");
  expect(screen.getByText("页序超出范围，本 PDF 共 9 页。")).toBeDefined();
  await userEvent.clear(input);
  await userEvent.type(input, "3");
  expect(
    screen.getByText(
      "该页尚未通过原图核对，暂不能设置跳转。可先查看阅读底稿或处理待核对内容。",
    ),
  ).toBeDefined();
});
