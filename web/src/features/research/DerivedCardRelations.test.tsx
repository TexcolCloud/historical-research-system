// @vitest-environment happy-dom
import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import DerivedCardRelations from "./DerivedCardRelations";

afterEach(cleanup);
test("incoming relationships expose actual endpoints, a separate evidence packet and stale-source warning", () => {
  render(
    <MemoryRouter>
      <DerivedCardRelations
        relations={{
          truncated: false,
          items: [
            {
              id: "edge",
              source_id: "first-card",
              target_id: "second-card",
              card_id: "evidence-packet",
              card_version: 2,
              kind: "reply_to",
              predicate: "复函",
              reason: "后文明确回复前函",
              role: "association",
              book_id: "book",
              run_id: "run",
              source_current: true,
              target_current: false,
              needs_update: true,
              target_card_id: "second-card",
              evidence: [],
              event_status: "unknown",
              status_note: "",
            },
          ],
        }}
      />
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("link", { name: "核对关系起点材料" }).getAttribute("href"),
  ).toContain("card=first-card");
  expect(
    screen.getByRole("link", { name: "核对关系终点材料" }).getAttribute("href"),
  ).toContain("card=second-card");
  expect(
    screen
      .getByRole("link", { name: "查看独立核验的关系证据包" })
      .getAttribute("href"),
  ).toContain("card=evidence-packet");
  expect(
    screen.getByText("关系的一端已有新版本，请重新核验后引用。"),
  ).toBeTruthy();
});
