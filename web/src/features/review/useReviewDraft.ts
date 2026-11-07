import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { client } from "@/client/api.ts";
import { queryKeys } from "@/client/query-keys.ts";
import type { components } from "@/client/schema.d.ts";
import {
  serializeReviewLocations,
  type ReviewLocation,
} from "@/editor/review-highlights.ts";
import type { Resolution } from "./ReviewFocus.tsx";
import { mergeReviewIssue } from "./review-cache.ts";

type Issue = components["schemas"]["ReviewIssue"];
type Receipt = components["schemas"]["DraftReceipt"];
export type DraftSnapshot = {
  text: string;
  resolutions: Resolution[];
  locations: ReviewLocation[];
};
const input = (issue: Issue) => issue.review_input_sha256 || issue.text_sha256;
const signature = (snapshot: DraftSnapshot) =>
  JSON.stringify([
    snapshot.text,
    snapshot.resolutions,
    serializeReviewLocations(snapshot.locations),
  ]);

/** Serializes one page's writes; a newer intent supersedes queued older snapshots. */
export function useReviewDraft(
  issue: Issue,
  snapshot: DraftSnapshot,
  hold: boolean,
  sourceConflict: boolean,
  restore: (value: Issue) => DraftSnapshot,
) {
  const query = useQueryClient();
  const revision = useRef(issue.revision);
  const saved = useRef(
    snapshot.text === (issue.draft_text ?? issue.text) &&
      JSON.stringify(snapshot.resolutions) ===
        JSON.stringify(issue.resolutions ?? [])
      ? signature(snapshot)
      : "",
  );
  const savedText = useRef(issue.draft_text ?? issue.text);
  const savedResolutions = useRef(JSON.stringify(issue.resolutions ?? []));
  const latest = useRef(snapshot);
  latest.current = snapshot;
  const issueRef = useRef(issue);
  issueRef.current = issue;
  const baseInput = useRef(input(issue));
  const restoreRef = useRef(restore);
  restoreRef.current = restore;
  const holding = useRef(hold);
  holding.current = hold;
  const generation = useRef(0);
  const pending = useRef(0);
  const tail = useRef<Promise<unknown>>(Promise.resolve());
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [remote, setRemote] = useState<Issue | null>(null);
  const remoteRef = useRef(remote);
  remoteRef.current = remote;
  const [readOnly, setReadOnly] = useState(issue.state !== "pending");
  const readOnlyRef = useRef(readOnly);
  readOnlyRef.current = readOnly;
  function cancelScheduled() {
    clearTimeout(timer.current);
    timer.current = undefined;
  }
  function supersede() {
    cancelScheduled();
    return ++generation.current;
  }
  function accept(value: Issue, keepLocal: boolean) {
    supersede();
    revision.current = value.revision;
    savedText.current = value.draft_text ?? value.text;
    savedResolutions.current = JSON.stringify(value.resolutions ?? []);
    if (!keepLocal) saved.current = signature(restoreRef.current(value));
    setRemote(null);
    remoteRef.current = null;
    setReadOnly(value.state !== "pending");
    readOnlyRef.current = value.state !== "pending";
  }
  function matchesSaved() {
    return (
      latest.current.text === savedText.current &&
      JSON.stringify(latest.current.resolutions) === savedResolutions.current
    );
  }
  useEffect(() => {
    if (issue.state !== "pending") {
      supersede();
      setReadOnly(true);
      readOnlyRef.current = true;
      return;
    }
    if (
      issue.revision <= revision.current ||
      input(issue) !== baseInput.current
    )
      return;
    if (pending.current || holding.current || !matchesSaved()) {
      cancelScheduled();
      setRemote(issue);
      remoteRef.current = issue;
    } else accept(issue, false);
    // A same-input GET may update the draft without remounting this session.
  }, [issue]);

  async function refreshAfterConflict() {
    const current = issueRef.current;
    const { data: fresh } = await client.GET("/api/v2/reviews/{issue_id}", {
      params: { path: { issue_id: current.id } },
    });
    if (!fresh) return;
    let observed = mergeReviewIssue(current, issueRef.current);
    const cached = query.getQueryData<Issue>(queryKeys.issue(current.id));
    if (cached) observed = mergeReviewIssue(observed, cached);
    await query.cancelQueries({ queryKey: queryKeys.issue(current.id) });
    const retained =
      query.setQueryData<Issue>(queryKeys.issue(current.id), (existing) =>
        mergeReviewIssue(
          existing ? mergeReviewIssue(observed, existing) : observed,
          fresh,
        ),
      ) ?? observed;
    if (retained.state !== "pending") {
      supersede();
      setRemote(null);
      remoteRef.current = null;
      setReadOnly(true);
      readOnlyRef.current = true;
      void query.invalidateQueries({
        queryKey: queryKeys.issues(current.run_id),
      });
    } else {
      cancelScheduled();
      setRemote(retained);
      remoteRef.current = retained;
    }
    return retained;
  }

  const draft = useMutation({
    mutationKey: ["platform", "review-write", issue.run_id],
    mutationFn: async (value: DraftSnapshot): Promise<Receipt> => {
      const current = issueRef.current;
      if (readOnlyRef.current || current.state !== "pending")
        throw new Error("本页已经提交，正在更新待核对清单；本地输入仍保留。");
      if (remoteRef.current)
        throw new Error(
          "服务器草稿已有更新。本页输入已保留，请先选择要继续核对的版本。",
        );
      const { data, response } = await client.PUT(
        "/api/v2/reviews/{issue_id}/draft",
        {
          params: { path: { issue_id: current.id } },
          body: {
            text: value.text,
            resolutions: value.resolutions,
            locations: serializeReviewLocations(value.locations),
            expected_revision: revision.current,
          },
        },
      );
      if (!data) {
        if (response.status === 409) {
          const fresh = await refreshAfterConflict();
          if (fresh && fresh.state !== "pending")
            throw new Error(
              "本页已经提交，正在更新待核对清单；本地输入仍保留。",
            );
          throw new Error(
            "服务器草稿已有更新。本页输入已保留，请核对最新草稿后继续。",
          );
        }
        throw new Error(
          response.status === 422
            ? "当前文字或疑点定位未通过校验。本页输入已保留，请检查后重试。"
            : "草稿保存失败，本页输入仍在当前窗口。请重试保存后再离开。",
        );
      }
      const updated = [
        remoteRef.current,
        query.getQueryData<Issue>(queryKeys.issue(current.id)),
      ].find(
        (value) =>
          value &&
          (value.state !== "pending" ||
            value.revision > data.revision ||
            input(value) !== input(current)),
      );
      if (updated) {
        cancelScheduled();
        if (updated.state !== "pending") {
          readOnlyRef.current = true;
          setReadOnly(true);
          throw new Error("本页已经提交，正在更新待核对清单；本地输入仍保留。");
        }
        remoteRef.current = updated;
        setRemote(updated);
        throw new Error(
          "服务器草稿已有更新。本页输入已保留，请核对最新草稿后继续。",
        );
      }
      if (remoteRef.current) {
        remoteRef.current = null;
        setRemote(null);
      }
      revision.current = data.revision;
      savedText.current = data.draft_text;
      savedResolutions.current = JSON.stringify(
        data.resolutions ?? value.resolutions,
      );
      saved.current = signature({
        ...value,
        text: data.draft_text,
        resolutions: data.resolutions ?? value.resolutions,
      });
      query.setQueryData<Issue>(queryKeys.issue(current.id), (existing) => {
        if (
          existing &&
          (input(existing) !== input(current) ||
            existing.revision > data.revision ||
            existing.state !== "pending")
        )
          return existing;
        return {
          ...current,
          draft_conflict: false,
          revision: data.revision,
          draft_text: data.draft_text,
          resolutions: data.resolutions ?? value.resolutions,
          draft_locations: data.locations,
          draft_locations_text_sha256: data.locations_text_sha256,
        };
      });
      return data;
    },
  });
  const mutateRef = useRef(draft.mutateAsync);
  mutateRef.current = draft.mutateAsync;
  function enqueue(value: DraftSnapshot, intent: number) {
    pending.current += 1;
    const next = tail.current
      .catch(() => undefined)
      .then(async () => {
        const current = query.getQueryData<Issue>(
          queryKeys.issue(issueRef.current.id),
        );
        if (
          intent !== generation.current ||
          readOnlyRef.current ||
          (current && current.state !== "pending")
        )
          return;
        if (signature(value) === saved.current) return;
        await mutateRef.current(value);
      })
      .finally(() => {
        pending.current -= 1;
      });
    tail.current = next;
    return next;
  }
  function save(value: DraftSnapshot) {
    const intent = supersede();
    return enqueue(value, intent).then(() => {
      const current = query.getQueryData<Issue>(
        queryKeys.issue(issueRef.current.id),
      );
      if (readOnlyRef.current || (current && current.state !== "pending"))
        throw new Error("本页已经提交，正在更新待核对清单；本地输入仍保留。");
      if (intent !== generation.current || remoteRef.current)
        throw new Error(
          "本页状态已有更新。本页输入已保留，请核对当前状态后继续。",
        );
    });
  }
  const saveRef = useRef(enqueue);
  saveRef.current = enqueue;
  useEffect(() => {
    cancelScheduled();
    if (
      hold ||
      sourceConflict ||
      remote ||
      readOnly ||
      draft.isError ||
      signature(snapshot) === saved.current
    )
      return;
    const intent = generation.current;
    timer.current = setTimeout(() => {
      timer.current = undefined;
      void saveRef.current(snapshot, intent).catch(() => undefined);
    }, 800);
    return cancelScheduled;
  }, [
    snapshot.text,
    snapshot.resolutions,
    snapshot.locations,
    hold,
    sourceConflict,
    remote,
    readOnly,
    draft.isError,
  ]);
  const conflictRef = useRef(sourceConflict);
  conflictRef.current = sourceConflict;
  useEffect(
    () => () => {
      cancelScheduled();
      if (
        !conflictRef.current &&
        !remoteRef.current &&
        !readOnlyRef.current &&
        !holding.current &&
        !matchesSaved()
      )
        void saveRef
          .current(latest.current, generation.current)
          .catch(() => undefined);
    },
    [],
  );
  return {
    draft,
    save,
    revision,
    remote,
    readOnly,
    refreshAfterConflict,
    changed: supersede,
    resolveRemote: (keepLocal: boolean) => {
      if (remoteRef.current) accept(remoteRef.current, keepLocal);
      draft.reset();
    },
    markSubmitted: () => {
      supersede();
      readOnlyRef.current = true;
      setReadOnly(true);
    },
  };
}
