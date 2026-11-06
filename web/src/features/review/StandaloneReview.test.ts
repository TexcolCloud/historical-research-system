// @vitest-environment happy-dom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, expect, test, vi } from "vitest";

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

test("the packaged standalone script gates per-doubt decisions and locates Unicode draft text", () => {
  document.body.dataset.workspaceId = "technical-workspace";
  document.body.innerHTML = `<p data-sync-status></p><form data-approval-ready="true">
    <p data-review-completion></p><details data-review-doubt="one"><summary>疑点</summary>
    <input type="radio" name="resolution.one" value="keep"><input type="radio" name="resolution.one" value="edit">
    <button type="button" data-locate-anchor='{"exact":"甲错乙","prefix":"😀","suffix":""}' data-locate-focus="错">定位</button></details>
    <textarea name="markdown">😀甲错乙</textarea><button name="verdict" value="approved">提交</button>
    </form>`;
  vi.stubGlobal("fetch", () => new Promise(() => {}));
  const module = readFileSync(
    resolve(
      import.meta.dirname,
      "../../../../src/hrs/codex/workbench_assets.py",
    ),
    "utf-8",
  );
  const script = module.match(/SCRIPT = r"""([\s\S]*)"""/)![1];
  new Function(script)();
  const submit = document.querySelector<HTMLButtonElement>(
    "button[value=approved]",
  )!;
  expect(submit.disabled).toBe(true);
  const choice = document.querySelector<HTMLInputElement>("input[value=keep]")!;
  choice.checked = true;
  choice.dispatchEvent(new Event("change", { bubbles: true }));
  expect(submit.disabled).toBe(false);
  document
    .querySelector<HTMLButtonElement>("button[data-locate-anchor]")!
    .click();
  const draft = document.querySelector<HTMLTextAreaElement>("textarea")!;
  expect(draft.selectionStart).toBe(3);
  expect(draft.selectionEnd).toBe(4);
  expect(draft.value).toBe("😀甲错乙");
  document.querySelector("form")!.dataset.approvalReady = "false";
  choice.dispatchEvent(new Event("change", { bubbles: true }));
  expect(submit.disabled).toBe(true);
});

test.each([
  ["true", "true", false],
  ["false", "true", true],
  ["true", "false", true],
])(
  "standalone directory final decisions honor scope %s and target %s",
  (scope, target, disabled) => {
    document.body.dataset.workspaceId = "synthetic-workspace";
    document.body.innerHTML = `<p data-sync-status></p><form method="post" data-approval-ready="${scope}"><button name="verdict" value="link" data-final-decision data-target-ready="${target}">确认跳转</button></form>`;
    vi.stubGlobal("fetch", () => new Promise(() => {}));
    const module = readFileSync(
      resolve(
        import.meta.dirname,
        "../../../../src/hrs/codex/workbench_assets.py",
      ),
      "utf-8",
    );
    new Function(module.match(/SCRIPT = r"""([\s\S]*)"""/)![1])();
    expect(document.querySelector<HTMLButtonElement>("button")!.disabled).toBe(
      disabled,
    );
  },
);
