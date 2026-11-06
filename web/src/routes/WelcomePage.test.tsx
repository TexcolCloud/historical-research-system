// @vitest-environment happy-dom
import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import WelcomePage from "./WelcomePage";

afterEach(cleanup);

test("feature preview supports note inspection and scoped example search without importing a document", async () => {
  render(
    <MemoryRouter>
      <WelcomePage />
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  expect(
    screen.getByRole("link", { name: "开始本机试用" }).getAttribute("href"),
  ).toBe("/");
  await user.click(screen.getByRole("tab", { name: "阅读" }));
  await user.click(screen.getByRole("button", { name: "查看样例出处" }));
  expect(screen.getByText(/样例来源说明，非原书脚注/)).toBeTruthy();
  await user.click(screen.getByRole("tab", { name: "检索" }));
  const query = screen.getByRole("textbox", { name: "试试一个关键词" });
  await user.clear(query);
  await user.type(query, "不存在的内容");
  expect(
    screen.getByRole("heading", { name: "没有匹配的示例文字" }),
  ).toBeTruthy();
  await user.clear(query);
  await user.type(query, "初春");
  expect(screen.getByText("1 条示例结果 · 影印第 21 页")).toBeTruthy();
});
