import { queryKeys } from "@/client/query-keys.ts";
/** Owns draft serialization, evidence locations and final decision receipts. */
import { useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { client } from "@/client/api.ts";
import type { components } from "@/client/schema.d.ts";
import {
  decisionRange,
  readLocations,
  suggestionEdit,
  moveReviewLocations,
  recoverReviewLocations,
  sourceRanges,
  type ReviewLocation,
} from "@/editor/review-highlights.ts";
import type { Resolution } from "./ReviewFocus.tsx";
import type { RuntimeScope } from "@/app/runtime-scope.ts";
import { useReviewDraft } from "./useReviewDraft.ts";

type EditorMode =
  | { kind: "read" }
  | { kind: "focus"; range: { start: number; end: number } }
  | { kind: "page"; source: boolean };

type Issue = components["schemas"]["ReviewIssue"];
export function useReviewSession(
  issue: Issue,
  onSaved: (
    receipt: components["schemas"]["ReviewReceipt"],
  ) => void | Promise<void>,
  runtimeScope: RuntimeScope = "content",
) {
  const query = useQueryClient(),
    draftKey = ["platform", "review-edit", issue.id];
  const cached = query.getQueryData<
    | string
    | {
        text: string;
        locations: ReviewLocation[];
        baseTextSha?: string;
        baseReviewInput?: string;
      }
  >(draftKey);
  const reviewInput =
    ("review_input_sha256" in issue &&
      typeof issue.review_input_sha256 === "string" &&
      issue.review_input_sha256) ||
    issue.text_sha256;
  const draftConflict =
    ("draft_conflict" in issue && issue.draft_conflict === true) ||
    (typeof cached === "object" &&
      (cached.baseTextSha !== issue.text_sha256 ||
        cached.baseReviewInput !== reviewInput));
  const [conflictUntouched, setConflictUntouched] = useState(
    Boolean(draftConflict),
  );
  const [text, setText] = useState(
    (typeof cached === "string" ? cached : cached?.text) ??
      issue.draft_text ??
      issue.text,
  );
  const [resolutions, setResolutions] = useState<Resolution[]>(
    draftConflict ? [] : (issue.resolutions ?? []),
  );
  const baseLocations = useMemo(
    () => readLocations(issue.locations ?? [], issue.text, issue.text_sha256),
    [issue.locations, issue.text, issue.text_sha256],
  );
  const [textLocations, setTextLocations] = useState(() => {
    if (cached && typeof cached !== "string" && !draftConflict)
      return cached.locations;
    const restored =
      !draftConflict &&
      text === issue.draft_text &&
      issue.draft_locations_text_sha256
        ? readLocations(
            issue.draft_locations ?? [],
            text,
            issue.draft_locations_text_sha256,
          )
        : [];
    return recoverReviewLocations(issue.text, text, baseLocations).map(
      (location) =>
        restored.find((saved) => saved.id === location.id) ?? location,
    );
  });
  const [active, setActive] = useState(
    () =>
      baseLocations.find(
        (l) =>
          draftConflict ||
          !issue.resolutions?.some(
            (r) => r.id === l.id && r.action !== "defer",
          ),
      )?.id ?? "",
  );
  const [boxes, setBoxes] = useState<Record<string, number[]>>({});
  const locations = useMemo(
    () =>
      textLocations.map<ReviewLocation>((l) =>
        boxes[l.id]
          ? { ...l, boxes: [boxes[l.id]], image_precision: "region" }
          : l,
      ),
    [textLocations, boxes],
  );
  const current = locations.find((l) => l.id === active);
  const [editorMode, setEditorMode] = useState<EditorMode>({ kind: "read" });
  const editing = editorMode.kind !== "read";
  const wholePage = editorMode.kind === "page";
  const editRange = editorMode.kind === "focus" ? editorMode.range : null;
  const sourceMode = editorMode.kind === "page" && editorMode.source;
  const [pageReviewed, setPageReviewed] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const snapshot = useRef({ text, resolutions, locations: textLocations });
  function restoreServer(value: Issue) {
    const nextText = value.draft_text ?? value.text;
    const source = readLocations(
      value.locations ?? [],
      value.text,
      value.text_sha256,
    );
    const restored = value.draft_locations_text_sha256
      ? readLocations(
          value.draft_locations ?? [],
          nextText,
          value.draft_locations_text_sha256,
        )
      : [];
    const nextLocations = recoverReviewLocations(
      value.text,
      nextText,
      source,
    ).map(
      (location) =>
        restored.find((saved) => saved.id === location.id) ?? location,
    );
    const decisions = value.resolutions ?? [];
    setText(nextText);
    setResolutions(decisions);
    setTextLocations(nextLocations);
    setBoxes({});
    setActive((selected) =>
      nextLocations.some(
        (location) =>
          location.id === selected &&
          !decisions.some(
            (decision) =>
              decision.id === selected && decision.action !== "defer",
          ),
      )
        ? selected
        : (nextLocations.find(
            (location) =>
              !decisions.some(
                (decision) =>
                  decision.id === location.id && decision.action !== "defer",
              ),
          )?.id ?? ""),
    );
    setEditorMode({ kind: "read" });
    setPageReviewed(false);
    query.removeQueries({ queryKey: draftKey });
    return { text: nextText, resolutions: decisions, locations: nextLocations };
  }
  const {
    draft,
    save,
    revision,
    remote,
    readOnly,
    resolveRemote,
    markSubmitted,
    refreshAfterConflict,
    changed: changedDraft,
  } = useReviewDraft(
    issue,
    { text, resolutions, locations: textLocations },
    finishing,
    conflictUntouched,
    restoreServer,
  );
  function saveDraft(
    value: string,
    decisions = resolutions,
    positionSnapshot = textLocations,
  ) {
    return save({
      text: value,
      resolutions: decisions,
      locations: positionSnapshot,
    });
  }
  const finishingLock = useRef(false);

  function changeText(value: string) {
    if (finishingLock.current || readOnly) return;
    changedDraft();
    setConflictUntouched(false);
    const nextLocations = moveReviewLocations(text, value, textLocations);
    const retained = resolutions.filter((r) => {
      const location = textLocations.find((l) => l.id === r.id),
        nextLocation = nextLocations.find((l) => l.id === r.id);
      if (!location || !nextLocation) return false;
      const before = location.text_spans?.length
          ? sourceRanges(text, location)
          : [decisionRange(text, location)],
        after = nextLocation.text_spans?.length
          ? sourceRanges(value, nextLocation)
          : [decisionRange(value, nextLocation)];
      return (
        before.length === after.length &&
        before.every((range, index) => {
          const nextRange = after[index];
          return Boolean(
            range &&
            nextRange &&
            text.slice(range.start, range.end) ===
              value.slice(nextRange.start, nextRange.end),
          );
        })
      );
    });
    setText(value);
    setResolutions(retained);
    setTextLocations(nextLocations);
    setPageReviewed(false);
    query.setQueryData(draftKey, {
      text: value,
      locations: nextLocations,
      baseTextSha: issue.text_sha256,
      baseReviewInput: reviewInput,
    });
    draft.reset();
  }
  function beginEdit() {
    if (finishingLock.current || readOnly) return;
    snapshot.current = { text, resolutions, locations: textLocations };
    const range = current ? decisionRange(text, current) : null;
    setEditorMode(
      range ? { kind: "focus", range } : { kind: "page", source: false },
    );
  }
  function changeFocus(value: string) {
    if (!editRange || finishingLock.current || readOnly) return;
    changeText(
      text.slice(0, editRange.start) + value + text.slice(editRange.end),
    );
    setEditorMode({
      kind: "focus",
      range: { ...editRange, end: editRange.start + value.length },
    });
  }
  function cancelEdit() {
    if (finishingLock.current || readOnly) return;
    const previous = snapshot.current;
    setText(previous.text);
    query.setQueryData(draftKey, {
      text: previous.text,
      locations: previous.locations,
      baseTextSha: issue.text_sha256,
      baseReviewInput: reviewInput,
    });
    setResolutions(previous.resolutions);
    setTextLocations(previous.locations);
    setEditorMode({ kind: "read" });
    void saveDraft(
      previous.text,
      previous.resolutions,
      previous.locations,
    ).catch(() => undefined);
  }
  async function finish(action: Resolution["action"]) {
    if (finishingLock.current || readOnly || issue.state !== "pending") return;
    finishingLock.current = true;
    setFinishing(true);
    setConflictUntouched(false);
    const decisions = current
      ? [
          ...resolutions.filter((r) => r.id !== current.id),
          { id: current.id, action },
        ]
      : resolutions;
    try {
      await saveDraft(text, decisions);
      setResolutions(decisions);
      setEditorMode({ kind: "read" });
      setPageReviewed(true);
      const next = locations.find(
        (l) => !decisions.some((r) => r.id === l.id && r.action !== "defer"),
      );
      setActive(next?.id ?? "");
    } finally {
      finishingLock.current = false;
      setFinishing(false);
    }
  }
  function editWholePage() {
    setEditorMode({ kind: "page", source: true });
  }
  function toggleSourceMode() {
    if (editorMode.kind === "page")
      setEditorMode({ kind: "page", source: !editorMode.source });
  }
  function recheckPage() {
    if (finishingLock.current || readOnly) return;
    changedDraft();
    setPageReviewed(false);
    const first = current ?? locations[0];
    if (first) {
      setResolutions(
        resolutions.filter((resolution) => resolution.id !== first.id),
      );
      setActive(first.id);
    }
  }
  const ready =
    !remote &&
    (locations.length
      ? locations.every((l) =>
          resolutions.some((r) => r.id === l.id && r.action !== "defer"),
        )
      : pageReviewed);
  const originalLocation = baseLocations.find((l) => l.id === current?.id);
  const proposal = originalLocation
    ? suggestionEdit(issue.text, originalLocation)
    : null;
  const request = useRef<components["schemas"]["ReviewDecision"] | null>(null);
  const changed = text !== issue.text;
  const submission = useMutation({
    mutationKey: ["platform", "review-write", issue.run_id],
    mutationFn: async () => {
      if (readOnly || issue.state !== "pending")
        throw new Error("本页已经提交，请继续核对下一页。");
      if (remote) throw new Error("请先处理服务器草稿更新，本页输入已保留。");
      if (runtimeScope === "unknown")
        throw new Error("工作区范围未确认，请重新确认后提交；当前草稿保留。");
      await saveDraft(text);
      const action = changed ? "correct" : "confirm";
      if (
        !request.current ||
        request.current.expected_revision !== revision.current ||
        request.current.text !== (changed ? text : undefined) ||
        JSON.stringify(request.current.resolutions ?? []) !==
          JSON.stringify(resolutions)
      )
        request.current = {
          decision_id: crypto.randomUUID(),
          expected_revision: revision.current,
          expected_text_sha256: issue.text_sha256,
          action,
          ...(changed ? { text } : {}),
          ...(locations.length ? { resolutions } : {}),
        };
      const decision = {
        ...request.current,
        decision_scope:
          runtimeScope === "workflow_only"
            ? ("workflow_test" as const)
            : ("content" as const),
      };
      const { data, response } = await client.POST(
        "/api/v2/reviews/{issue_id}/decisions",
        { params: { path: { issue_id: issue.id } }, body: decision },
      );
      if (!data) {
        if (response.status === 409) {
          const fresh = await refreshAfterConflict();
          throw new Error(
            fresh && fresh.state !== "pending"
              ? "本页已经提交，正在更新待核对清单；本地输入仍保留。"
              : "服务器草稿已有更新。本页输入已保留，请核对最新草稿后继续。",
          );
        }
        throw new Error(
          "尚未收到本页提交回执。草稿已保存，请重试提交以确认结果。",
        );
      }
      return data;
    },
    onSuccess: async (receipt) => {
      markSubmitted();
      query.removeQueries({ queryKey: draftKey });
      await onSaved(receipt);
    },
  });
  const busy = finishing || submission.isPending || readOnly;
  const completed = resolutions.filter(
    (r) => r.action !== "defer" && locations.some((l) => l.id === r.id),
  ).length;
  return {
    text,
    resolutions,
    locations,
    current,
    editing,
    wholePage,
    editRange,
    finishing,
    sourceMode,
    draft,
    ready,
    proposal,
    changed,
    submission,
    busy,
    completed,
    draftConflict,
    remoteDraft: remote,
    resolveRemote,
    readOnly,
    lastDoubt: !locations.some(
      (location) =>
        location.id !== current?.id &&
        !resolutions.some(
          (resolution) =>
            resolution.id === location.id && resolution.action !== "defer",
        ),
    ),
    recheckPage,
    setActive,
    finish,
    beginEdit,
    changeFocus,
    editWholePage,
    toggleSourceMode,
    changeText,
    cancelEdit,
    saveDraft,
    setBoxes,
  };
}
