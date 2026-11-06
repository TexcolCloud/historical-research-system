// @vitest-environment happy-dom
import { expect, test, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import WorkspaceBoundary from "./WorkspaceBoundary.tsx";

test("a failed page module provides a recovery action instead of a blank workspace", () => {
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  function BrokenPage(): never {
    throw new Error("Failed to fetch dynamically imported module");
  }
  render(
    <WorkspaceBoundary>
      <BrokenPage />
    </WorkspaceBoundary>,
  );
  expect(screen.getByRole("alert")).toBeDefined();
  expect(screen.getByRole("button", { name: "刷新页面" })).toBeDefined();
  expect(screen.getByText(/已保存的草稿/)).toBeDefined();
  cleanup();
  consoleError.mockRestore();
});
