import { queryKeys } from "@/client/query-keys.ts";
/** Owns draft serialization, evidence locations and final decision receipts. */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { client } from "@/client/api.ts";
import type { components } from "@/client/schema.d.ts";
import {
  decisionRange,
  readLocations,
  suggestionEdit,
  moveReviewLocations,
  recoverReviewLocations,
  serializeReviewLocations,
  sourceRanges,
  type ReviewLocation,
} from "@/editor/review-highlights.ts";
import type { Resolution } from "./ReviewFocus.tsx";
import type { RuntimeScope } from "@/app/runtime-scope.ts";

type EditorMode =
  | { kind: "read" }
  | { kind: "focus"; range: { start: number; end: number } }
  | { kind: "page"; source: boolean };

type Issue = components["schemas"]["ReviewIssue"];
export function useReviewSession(
  issue: Issue,
  onSaved: (receipt: components["schemas"]["ReviewReceipt"]) => void,
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
  const conflictUntouchedRef = useRef(conflictUntouched);
  conflictUntouchedRef.current = conflictUntouched;
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
      )?.id ??
      baseLocations[0]?.id ??
      "",
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
  const current = locations.find((l) => l.id === active) ?? locations[0];
  const [editorMode, setEditorMode] = useState<EditorMode>({ kind: "read" });
  const editing = editorMode.kind !== "read";
  const wholePage = editorMode.kind === "page";
  const editRange = editorMode.kind === "focus" ? editorMode.range : null;
  const sourceMode = editorMode.kind === "page" && editorMode.source;
  const [pageReviewed, setPageReviewed] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const snapshot = useRef({ text, resolutions, locations: textLocations });
  const latestText = useRef(text),
    latestResolutions = useRef(resolutions),
    latestLocations = useRef(textLocations);
  const savedText = useRef(issue.draft_text ?? issue.text),
    savedResolutions = useRef(JSON.stringify(issue.resolutions ?? [])),
    savedLocationsSha = useRef(issue.draft_locations_text_sha256);
  const revision = useRef(issue.revision),
    queue = useRef<Promise<unknown>>(Promise.resolve());
  latestText.current = text;
  latestResolutions.current = resolutions;
  latestLocations.current = textLocations;
  const draft = useMutation({
    mutationKey: ["platform", "review-write", issue.run_id],
    mutationFn: async ({
      value,
      decisions,
      positionSnapshot,
    }: {
      value: string;
      decisions: Resolution[];
      positionSnapshot: ReviewLocation[];
    }) => {
      if (
        value === savedText.current &&
        JSON.stringify(decisions) === savedResolutions.current
      )
        return {
          revision: revision.current,
          draft_text: value,
          resolutions: decisions,
          locations: positionSnapshot,
          locations_text_sha256: savedLocationsSha.current,
        };
      const { data, response } = await client.PUT(
        "/api/v2/reviews/{issue_id}/draft",
        {
          params: { path: { issue_id: issue.id } },
          body: {
            text: value,
            expected_revision: revision.current,
            resolutions: decisions,
            locations: serializeReviewLocations(positionSnapshot),
          },
        },
      );
      if (!data)
        throw new Error(
          response.status === 409
            ? "草稿版本已更新，本页输入尚未写入最新版本。请先复制本页文字，再刷新页面核对。"
            : "草稿保存失败，本页输入仍在当前窗口。请重试保存后再离开。",
        );
      revision.current = data.revision;
      savedText.current = data.draft_text;
      savedResolutions.current = JSON.stringify(decisions);
      savedLocationsSha.current = data.locations_text_sha256;
      return data;
    },
    onSuccess: (data) =>
      query.setQueryData<Issue>(queryKeys.issue(issue.id), (current) => {
        if (
          current &&
          (current.text_sha256 !== issue.text_sha256 ||
            ("review_input_sha256" in current &&
              "review_input_sha256" in issue &&
              current.review_input_sha256 !== issue.review_input_sha256) ||
            current.revision > data.revision)
        )
          return current;
        return {
          ...issue,
          draft_conflict: false,
          revision: data.revision,
          draft_text: data.draft_text,
          resolutions: data.resolutions,
          draft_locations: data.locations
            ? serializeReviewLocations(data.locations)
            : data.locations,
          draft_locations_text_sha256: data.locations_text_sha256,
        };
      }),
  });
  function saveDraft(
    value: string,
    decisions = latestResolutions.current,
    positionSnapshot = latestLocations.current,
  ) {
    const next = queue.current
      .catch(() => undefined)
      .then(() => draft.mutateAsync({ value, decisions, positionSnapshot }));
    queue.current = next;
    return next;
  }
  const saveRef = useRef(saveDraft);
  saveRef.current = saveDraft;
  useEffect(() => {
    if (
      (text === savedText.current &&
        JSON.stringify(resolutions) === savedResolutions.current) ||
      draft.isError ||
      conflictUntouched
    )
      return;
    const timer = setTimeout(() => {
      void saveRef
        .current(text, resolutions, textLocations)
        .catch(() => undefined);
    }, 800);
    return () => clearTimeout(timer);
  }, [text, resolutions, textLocations, draft.isError, conflictUntouched]);
  useEffect(
    () => () => {
      if (
        !conflictUntouchedRef.current &&
        (latestText.current !== savedText.current ||
          JSON.stringify(latestResolutions.current) !==
            savedResolutions.current)
      )
        void saveRef.current(latestText.current).catch(() => undefined);
    },
    [],
  );

  function changeText(value: string) {
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
    latestText.current = value;
    setResolutions(retained);
    latestResolutions.current = retained;
    setTextLocations(nextLocations);
    latestLocations.current = nextLocations;
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
    snapshot.current = { text, resolutions, locations: textLocations };
    const range = current ? decisionRange(text, current) : null;
    setEditorMode(
      range ? { kind: "focus", range } : { kind: "page", source: false },
    );
  }
  function changeFocus(value: string) {
    if (!editRange) return;
    changeText(
      text.slice(0, editRange.start) + value + text.slice(editRange.end),
    );
    setEditorMode({
      kind: "focus",
      range: { ...editRange, end: editRange.start + value.length },
    });
  }
  function cancelEdit() {
    const previous = snapshot.current;
    setText(previous.text);
    latestText.current = previous.text;
    query.setQueryData(draftKey, {
      text: previous.text,
      locations: previous.locations,
      baseTextSha: issue.text_sha256,
      baseReviewInput: reviewInput,
    });
    setResolutions(previous.resolutions);
    latestResolutions.current = previous.resolutions;
    setTextLocations(previous.locations);
    latestLocations.current = previous.locations;
    setEditorMode({ kind: "read" });
    void saveDraft(
      previous.text,
      previous.resolutions,
      previous.locations,
    ).catch(() => undefined);
  }
  async function finish(action: Resolution["action"]) {
    if (finishing) return;
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
      latestResolutions.current = decisions;
      setEditorMode({ kind: "read" });
      setPageReviewed(true);
      const next = locations.find(
        (l) => !decisions.some((r) => r.id === l.id && r.action !== "defer"),
      );
      if (next) setActive(next.id);
    } finally {
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
    setPageReviewed(false);
    if (current) {
      setResolutions(
        resolutions.filter((resolution) => resolution.id !== current.id),
      );
      setActive(current.id);
    }
  }
  const ready = locations.length
    ? locations.every((l) =>
        resolutions.some((r) => r.id === l.id && r.action !== "defer"),
      )
    : pageReviewed;
  const originalLocation = baseLocations.find((l) => l.id === current?.id);
  const proposal = originalLocation
    ? suggestionEdit(issue.text, originalLocation)
    : null;
  const request = useRef<components["schemas"]["ReviewDecision"] | null>(null);
  const changed = text !== issue.text;
  const submission = useMutation({
    mutationKey: ["platform", "review-write", issue.run_id],
    mutationFn: async () => {
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
      if (!data)
        throw new Error(
          response.status === 409
            ? "内容版本已更新，本页尚未提交。请先复制本页草稿，再刷新页面核对。"
            : "尚未收到本页提交回执。草稿已保存，请重试提交以确认结果。",
        );
      return data;
    },
    onSuccess: onSaved,
  });
  const busy = finishing || submission.isPending;
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
