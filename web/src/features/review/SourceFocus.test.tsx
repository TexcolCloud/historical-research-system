// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import SourceFocus from "./SourceFocus";
import type { ReviewLocation } from "@/editor/review-highlights";

afterEach(cleanup);

function doubt(precision: ReviewLocation["image_precision"]): ReviewLocation {
  return {
    id: "source-doubt",
    page: 1,
    boxes: [[0.1, 0.2, 0.9, 0.6]],
    image_precision: precision,
    anchor: { exact: "原文", prefix: "", suffix: "" },
    focus: "原文",
    message: "请核对原件",
    candidate: "原文",
    source_reading: "原文",
    suggestion: "",
  };
}

test.each([
  ["region", "175"],
  ["line", "100"],
] as const)(
  "%s source precision starts at %s percent without changing the box",
  (precision, zoom) => {
    const issue = doubt(precision);
    render(<SourceFocus src="/original.png" issue={issue} onBox={vi.fn()} />);
    const slider = screen.getByRole("slider", {
      name: "原图局部放大倍数",
    }) as HTMLInputElement;
    expect(slider.value).toBe(zoom);
    const rectangle = screen
      .getByLabelText("疑点局部原图")
      .querySelector("rect");
    expect(rectangle?.getAttribute("x")).toBe("0.1");
    expect(rectangle?.getAttribute("width")).toBe("0.8");
  },
);

test("a manual zoom survives selecting another doubt", () => {
  const issue = doubt("region");
  const view = render(
    <SourceFocus src="/original.png" issue={issue} onBox={vi.fn()} />,
  );
  fireEvent.change(screen.getByRole("slider", { name: "原图局部放大倍数" }), {
    target: { value: "225" },
  });
  view.rerender(
    <SourceFocus
      src="/original.png"
      issue={{ ...doubt("line"), id: "next-doubt" }}
      onBox={vi.fn()}
    />,
  );
  expect(
    (
      screen.getByRole("slider", {
        name: "原图局部放大倍数",
      }) as HTMLInputElement
    ).value,
  ).toBe("225");
});

test("a page without a box keeps the ordinary initial scale when a box is later selected", () => {
  const issue = { ...doubt("region"), boxes: [] };
  const view = render(
    <SourceFocus src="/original.png" issue={issue} onBox={vi.fn()} />,
  );
  expect(screen.queryByRole("slider", { name: "原图局部放大倍数" })).toBeNull();
  view.rerender(
    <SourceFocus src="/original.png" issue={doubt("region")} onBox={vi.fn()} />,
  );
  expect(
    (
      screen.getByRole("slider", {
        name: "原图局部放大倍数",
      }) as HTMLInputElement
    ).value,
  ).toBe("100");
});
