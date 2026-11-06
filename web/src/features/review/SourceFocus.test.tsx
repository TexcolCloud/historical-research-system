// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import SourceFocus from "./SourceFocus";
import type { ReviewLocation } from "@/editor/review-highlights";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

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

test.each(["region", "line"] as const)(
  "%s source precision starts with the complete original at 100 percent",
  (precision) => {
    const issue = doubt(precision);
    render(<SourceFocus src="/original.png" issue={issue} onBox={vi.fn()} />);
    const slider = screen.getByRole("slider", {
      name: "原图放大倍数",
    }) as HTMLInputElement;
    expect(slider.value).toBe("100");
    expect(screen.getByAltText("原书第 1 页").getAttribute("src")).toBe(
      "/original.png",
    );
    expect(screen.queryByLabelText("疑点局部原图")).toBeNull();
    const rectangle = screen.getByLabelText("原图疑点框").querySelector("rect");
    expect(rectangle?.getAttribute("x")).toBe("0.1");
    expect(rectangle?.getAttribute("width")).toBe("0.8");
  },
);

test("a manual zoom survives selecting another doubt", () => {
  const issue = doubt("region");
  const view = render(
    <SourceFocus src="/original.png" issue={issue} onBox={vi.fn()} />,
  );
  fireEvent.change(screen.getByRole("slider", { name: "原图放大倍数" }), {
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
        name: "原图放大倍数",
      }) as HTMLInputElement
    ).value,
  ).toBe("225");
});

test("a page without a box keeps the ordinary initial scale when a box is later selected", () => {
  const issue = { ...doubt("region"), boxes: [] };
  const view = render(
    <SourceFocus src="/original.png" issue={issue} onBox={vi.fn()} />,
  );
  expect(screen.getByRole("slider", { name: "原图放大倍数" })).toBeDefined();
  expect(screen.queryByRole("button", { name: "当前区域" })).toBeNull();
  view.rerender(
    <SourceFocus src="/original.png" issue={doubt("region")} onBox={vi.fn()} />,
  );
  expect(
    (
      screen.getByRole("slider", {
        name: "原图放大倍数",
      }) as HTMLInputElement
    ).value,
  ).toBe("100");
});

test("page precision never presents a box as a located region", () => {
  render(
    <SourceFocus src="/original.png" issue={doubt("page")} onBox={vi.fn()} />,
  );
  expect(screen.queryByRole("button", { name: "当前区域" })).toBeNull();
  expect(screen.getByLabelText("原图疑点框").querySelector("rect")).toBeNull();
  expect(screen.getByText(/没有可靠的局部定位/)).toBeDefined();
});

test.each([100, 225, 300])(
  "selecting a lower-page doubt reveals its original region inside the pane at %s percent without moving the page",
  async (zoom) => {
    const issue = doubt("region");
    const view = render(
      <SourceFocus src="/original.png" issue={issue} onBox={vi.fn()} />,
    );
    const pane = screen.getByLabelText("原图查看区");
    const image = screen.getByAltText("原书第 1 页");
    Object.defineProperties(image, {
      naturalWidth: { value: 400 },
      naturalHeight: { value: 800 },
    });
    const sheet = image.parentElement!;
    const slider = screen.getByRole("slider", {
      name: "原图放大倍数",
    }) as HTMLInputElement;
    Object.defineProperties(pane, {
      clientWidth: { value: 400 },
      clientHeight: { value: 300 },
    });
    vi.spyOn(pane, "getBoundingClientRect").mockReturnValue(
      new DOMRect(40, 100, 400, 300),
    );
    vi.spyOn(sheet, "getBoundingClientRect").mockImplementation(() => {
      const width = (400 * Number(slider.value)) / 100;
      return new DOMRect(
        40 - pane.scrollLeft,
        100 - pane.scrollTop,
        width,
        width * 2,
      );
    });
    const scroll = vi
      .spyOn(pane, "scrollTo")
      .mockImplementation((options: ScrollToOptions | number) => {
        if (typeof options !== "object") return;
        const width = (400 * Number(slider.value)) / 100;
        pane.scrollLeft = Math.max(
          0,
          Math.min(width - 400, options.left ?? pane.scrollLeft),
        );
        pane.scrollTop = Math.max(
          0,
          Math.min(width * 2 - 300, options.top ?? pane.scrollTop),
        );
      });
    const pageScroll = vi.spyOn(window, "scrollTo");
    fireEvent.change(slider, { target: { value: String(zoom) } });
    const lower = {
      ...issue,
      id: "lower-page",
      boxes: [[0.65, 0.8, 0.85, 0.9]],
    };
    view.rerender(
      <SourceFocus src="/original.png" issue={lower} onBox={vi.fn()} />,
    );
    await waitFor(() => expect(scroll).toHaveBeenCalled());
    const width = (400 * zoom) / 100;
    expect(pane.scrollTop).toBeGreaterThan(0);
    expect(0.8 * width * 2 - pane.scrollTop).toBeGreaterThanOrEqual(0);
    expect(0.9 * width * 2 - pane.scrollTop).toBeLessThanOrEqual(
      pane.clientHeight,
    );
    expect(0.65 * width - pane.scrollLeft).toBeGreaterThanOrEqual(0);
    expect(0.85 * width - pane.scrollLeft).toBeLessThanOrEqual(
      pane.clientWidth,
    );
    expect(slider.value).toBe(String(zoom));
    expect(pageScroll).not.toHaveBeenCalled();

    scroll.mockClear();
    pane.scrollTop = 12;
    view.rerender(
      <SourceFocus
        src="/original.png"
        issue={{ ...lower, message: "后台刷新" }}
        onBox={vi.fn()}
      />,
    );
    expect(scroll).not.toHaveBeenCalled();
    expect(pane.scrollTop).toBe(12);

    fireEvent.load(image);
    expect(scroll).toHaveBeenCalledOnce();
    expect(pane.scrollTop).toBeGreaterThan(12);
    expect(pageScroll).not.toHaveBeenCalled();
  },
);
