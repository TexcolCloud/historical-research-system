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
} from "@/editor/review-highlights.ts";
import type { Resolution } from "./ReviewFocus.tsx";

type EditorMode =
  | { kind: "read" }
  | { kind: "focus"; range: { start: number; end: number } }
  | { kind: "page"; source: boolean };

type Issue = components["schemas"]["ReviewIssue"];
export function useReviewSession(
  issue: Issue,
  onSaved: (receipt: components["schemas"]["ReviewReceipt"]) => void,
) {
  const query = useQueryClient(),
    draftKey = ["platform", "review-edit", issue.id];
  const [text, setText] = useState(
    query.getQueryData<string>(draftKey) ?? issue.draft_text ?? issue.text,
  );
  const [resolutions, setResolutions] = useState<Resolution[]>(
    issue.resolutions ?? [],
  );
  const baseLocations = useMemo(
    () => readLocations(issue.locations ?? []),
    [issue.locations],
  );
  const [textLocations, setTextLocations] = useState(() =>
    moveReviewLocations(issue.text, text, baseLocations),
  );
  const [active, setActive] = useState(
    () =>
      baseLocations.find(
        (l) =>
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
      textLocations.map((l) =>
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
    latestResolutions = useRef(resolutions);
  const savedText = useRef(issue.draft_text ?? issue.text),
    savedResolutions = useRef(JSON.stringify(issue.resolutions ?? []));
  const revision = useRef(issue.revision),
    queue = useRef<Promise<unknown>>(Promise.resolve());
  latestText.current = text;
  latestResolutions.current = resolutions;
  const draft = useMutation({
    mutationKey: ["platform", "review-write", issue.run_id],
    mutationFn: async ({
      value,
      decisions,
    }: {
      value: string;
      decisions: Resolution[];
    }) => {
      if (
        value === savedText.current &&
        JSON.stringify(decisions) === savedResolutions.current
      )
        return {
          revision: revision.current,
          draft_text: value,
          resolutions: decisions,
        };
      const { data, response } = await client.PUT(
        "/api/v2/reviews/{issue_id}/draft",
        {
          params: { path: { issue_id: issue.id } },
          body: {
            text: value,
            expected_revision: revision.current,
            resolutions: decisions,
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
      return data;
    },
    onSuccess: (data) =>
      query.setQueryData(queryKeys.issue(issue.id), {
        ...issue,
        revision: data.revision,
        draft_text: data.draft_text,
        resolutions: data.resolutions,
      }),
  });
  function saveDraft(value: string, decisions = latestResolutions.current) {
    const next = queue.current
      .catch(() => undefined)
      .then(() => draft.mutateAsync({ value, decisions }));
    queue.current = next;
    return next;
  }
  const saveRef = useRef(saveDraft);
  saveRef.current = saveDraft;
  useEffect(() => {
    if (
      (text === savedText.current &&
        JSON.stringify(resolutions) === savedResolutions.current) ||
      draft.isError
    )
      return;
    const timer = setTimeout(() => {
      void saveRef.current(text).catch(() => undefined);
    }, 800);
    return () => clearTimeout(timer);
  }, [text, resolutions, draft.isError]);
  useEffect(
    () => () => {
      if (
        latestText.current !== savedText.current ||
        JSON.stringify(latestResolutions.current) !== savedResolutions.current
      )
        void saveRef.current(latestText.current).catch(() => undefined);
    },
    [],
  );

  function changeText(value: string) {
    const nextLocations = moveReviewLocations(text, value, textLocations);
    const retained = resolutions.filter((r) => {
      const location = locations.find((l) => l.id === r.id);
      if (!location) return false;
      const before = decisionRange(text, location),
        after = decisionRange(
          value,
          nextLocations.find((l) => l.id === r.id) ?? location,
        );
      return (
        before &&
        after &&
        text.slice(before.start, before.end) ===
          value.slice(after.start, after.end)
      );
    });
    setText(value);
    latestText.current = value;
    setResolutions(retained);
    latestResolutions.current = retained;
    setTextLocations(nextLocations);
    setPageReviewed(false);
    query.setQueryData(draftKey, value);
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
    query.setQueryData(draftKey, previous.text);
    setResolutions(previous.resolutions);
    latestResolutions.current = previous.resolutions;
    setTextLocations(previous.locations);
    setEditorMode({ kind: "read" });
    void saveDraft(previous.text, previous.resolutions).catch(() => undefined);
  }
  async function finish(action: Resolution["action"]) {
    if (finishing) return;
    setFinishing(true);
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
      const { data, response } = await client.POST(
        "/api/v2/reviews/{issue_id}/decisions",
        { params: { path: { issue_id: issue.id } }, body: request.current },
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
