import { useEffect, useMemo, useState } from "react";
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { studies, studyKeys, studyRequest } from "./api";
import type { StudyCanvasDocument, StudyCanvasRecord } from "./types";

/** Workspace navigation and server resources; document editing has a separate session. */
export function useCanvasResources(topicId: string, canvasId?: string) {
  const [, setParams] = useSearchParams();
  const [archived, setArchived] = useState(false);
  const [createError, setCreateError] = useState("");
  const [creating, setCreating] = useState(false);
  const [importNotice, setImportNotice] = useState("");
  const cache = useQueryClient();
  const topic = useQuery({
    queryKey: studyKeys.topic(topicId),
    queryFn: ({ signal }) => studies.topic(topicId, signal),
    retry: false,
  });
  const records = useInfiniteQuery({
    queryKey: [...studyKeys.records(topicId), "canvas-drawer"],
    queryFn: ({ signal, pageParam }) =>
      studies.records(topicId, signal, false, { offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last) =>
      last.items.length && last.offset + last.items.length < last.total
        ? last.offset + last.items.length
        : undefined,
    retry: false,
  });
  const canvases = useQuery({
    queryKey: [...studyKeys.canvases(topicId), archived],
    queryFn: ({ signal }) => studies.canvases(topicId, signal, archived),
    retry: false,
  });
  const preferences = useQuery({
    queryKey: ["studies", "preferences"],
    queryFn: () =>
      studyRequest<{ restore_last_canvas: boolean }>("/preferences"),
    retry: false,
    staleTime: 30_000,
  });
  const runtime = useQuery({
    queryKey: ["platform", "runtime"],
    queryFn: async () => {
      const response = await fetch("/api/v2/runtime");
      if (!response.ok) throw new Error("无法确认当前工作区，请重新读取。");
      const value = await response.json();
      if (typeof value.workspace_id !== "string")
        throw new Error("工作区标识缺失，暂不打开可编辑画布。");
      return value as { workspace_id: string };
    },
    staleTime: 30_000,
    retry: false,
  });
  const lastCanvas = useMemo(() => {
    if (!runtime.data || !preferences.data?.restore_last_canvas) return "";
    try {
      const value = JSON.parse(
        localStorage.getItem(
          `hrs:study:last-canvas:${runtime.data.workspace_id}`,
        ) || "null",
      );
      return value?.topic_id === topicId &&
        typeof value.canvas_id === "string" &&
        value.canvas_id.length <= 100
        ? value.canvas_id
        : "";
    } catch {
      return "";
    }
  }, [runtime.data, preferences.data?.restore_last_canvas, topicId]);
  const activeId = canvasId || lastCanvas || canvases.data?.items[0]?.id;
  const canvas = useQuery({
    queryKey: studyKeys.canvas(activeId || ""),
    queryFn: ({ signal }) => studies.canvas(activeId!, signal),
    enabled: !!activeId,
    retry: false,
    refetchOnWindowFocus: false,
  });
  useEffect(() => {
    if (!runtime.data || !canvas.data || !preferences.data?.restore_last_canvas)
      return;
    try {
      localStorage.setItem(
        `hrs:study:last-canvas:${runtime.data.workspace_id}`,
        JSON.stringify({
          topic_id: topicId,
          canvas_id: canvas.data.id,
          title: canvas.data.title,
          updated_at: Date.now(),
        }),
      );
    } catch {
      /* A navigation hint never replaces saved canvas state. */
    }
  }, [
    runtime.data,
    canvas.data?.id,
    canvas.data?.title,
    preferences.data?.restore_last_canvas,
    topicId,
  ]);

  function selectCanvas(id: string) {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("canvas", id);
      next.delete("node");
      return next;
    });
  }
  async function createCanvas(title: string, document?: StudyCanvasDocument) {
    if (creating) return;
    setCreating(true);
    setCreateError("");
    try {
      const result = await studies.createCanvas(topicId, {
        title: title.trim() || "研究画布",
        ...(document ? { document } : {}),
      });
      cache.setQueryData(studyKeys.canvas(result.id), result);
      await cache.invalidateQueries({ queryKey: studyKeys.canvases(topicId) });
      setArchived(false);
      selectCanvas(result.id);
      return result;
    } catch (failure) {
      setCreateError(
        failure instanceof Error ? failure.message : "画布创建未完成，请重试。",
      );
    } finally {
      setCreating(false);
    }
  }
  async function importBackup(input: HTMLInputElement) {
    const file = input.files?.[0];
    if (!file || !topic.data || creating) return;
    if (file.size > 8_000_000) {
      setCreateError("备份超过 8 MB，请选择本工具导出的结构化备份。");
      input.value = "";
      return;
    }
    setCreating(true);
    setCreateError("");
    setImportNotice("");
    try {
      const backup: unknown = JSON.parse(await file.text());
      if (
        typeof backup !== "object" ||
        backup === null ||
        Array.isArray(backup)
      )
        throw new Error("备份结构无效，请使用完整 JSON 备份。");
      const originalCanvas =
        "canvas" in backup &&
        typeof backup.canvas === "object" &&
        backup.canvas !== null
          ? backup.canvas
          : undefined;
      const originalTitle =
        originalCanvas &&
        "title" in originalCanvas &&
        typeof originalCanvas.title === "string"
          ? originalCanvas.title.trim() || "研究画布"
          : "研究画布";
      const suffix = " · 恢复副本";
      const result = await studyRequest<
        StudyCanvasRecord & { import_warnings?: string[] }
      >(`/topics/${encodeURIComponent(topicId)}/canvases/import`, "POST", {
        expected_version: topic.data.version,
        backup,
        title: originalTitle.slice(0, 200 - suffix.length) + suffix,
      });
      cache.setQueryData(studyKeys.canvas(result.id), result);
      await Promise.all([
        cache.invalidateQueries({ queryKey: studyKeys.topic(topicId) }),
        cache.invalidateQueries({ queryKey: studyKeys.records(topicId) }),
        cache.invalidateQueries({ queryKey: studyKeys.canvases(topicId) }),
      ]);
      setArchived(false);
      selectCanvas(result.id);
      setImportNotice(
        [
          "已恢复为独立画布，个人记录已恢复为副本，原有画布与史料引用保留。",
          ...(result.import_warnings || []),
        ].join("；"),
      );
    } catch (failure) {
      setCreateError(
        failure instanceof Error
          ? failure.message
          : "恢复未完成，当前画布保留。",
      );
    } finally {
      input.value = "";
      setCreating(false);
    }
  }
  const failure =
    (!topic.data && topic.error) ||
    (!records.data && records.error) ||
    (!canvases.data && canvases.error) ||
    (!canvas.data && canvas.error) ||
    (!runtime.data && runtime.error);
  const ready =
    !!topic.data &&
    !!records.data &&
    !!canvases.data &&
    !!runtime.data &&
    !preferences.isPending &&
    (!activeId || !!canvas.data);
  const wrongTopic = !!canvas.data && canvas.data.topic_id !== topicId;
  return {
    data: ready
      ? {
          topic: topic.data!,
          canvas: canvas.data,
          canvases: canvases.data!.items,
          records: records.data!.pages.flatMap((page) => page.items),
          workspace: runtime.data!.workspace_id,
          activeId,
          recordPage: {
            hasMore: !!records.hasNextPage,
            loading: records.isFetchingNextPage,
            onMore: () => void records.fetchNextPage(),
            error: records.error?.message,
          },
        }
      : undefined,
    error: failure
      ? failure.message
      : wrongTopic
        ? "该画布不属于当前专题，请从专题中重新选择。"
        : "",
    archived,
    setArchived,
    creating,
    createError,
    importNotice,
    selectCanvas,
    createCanvas,
    importBackup,
    reload: () => {
      void topic.refetch();
      void records.refetch();
      void canvases.refetch();
      if (activeId) void canvas.refetch();
      void runtime.refetch();
    },
    onArchived: (result?: StudyCanvasRecord) => {
      setArchived(false);
      if (result && !result.archived) selectCanvas(result.id);
      else
        setParams((previous) => {
          const next = new URLSearchParams(previous);
          next.delete("canvas");
          next.delete("node");
          next.delete("edge");
          return next;
        });
    },
  };
}
