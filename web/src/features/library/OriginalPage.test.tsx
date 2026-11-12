// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as api from "@/client/api";
import OriginalPage from "./OriginalPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("returning to the same source-page view retains its image zoom", async () => {
  vi.spyOn(api, "readOriginalPage").mockResolvedValue(
    "data:image/png;base64,AA==",
  );
  const cache = new QueryClient();
  function mount() {
    return render(
      <QueryClientProvider client={cache}>
        <OriginalPage runId="run" page={7} />
      </QueryClientProvider>,
    );
  }
  const first = mount();
  await screen.findByAltText("PDF 第 7 页");
  fireEvent.change(screen.getByRole("slider", { name: "原图放大倍数" }), {
    target: { value: "175" },
  });
  first.unmount();
  mount();
  expect(
    screen.getByRole("slider", { name: "原图放大倍数" }).getAttribute("value"),
  ).toBe("175");
});
