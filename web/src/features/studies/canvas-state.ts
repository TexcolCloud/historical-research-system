import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { studies, StudyError, studyKeys } from "./api";
import {
  commitCanvas,
  createCanvasHistory,
  readCanvasBackup,
  redoCanvas,
  undoCanvas,
} from "./canvas-model";
import type { StudyCanvasDocument, StudyCanvasRecord } from "./types";
import { writeCanvasDocument } from "./canvas-write";

/** One editor session owns one version. Unknown write outcomes retain the local draft. */
export function useCanvasDocument(
  initial: StudyCanvasRecord,
  workspace: string,
) {
  const cache = useQueryClient();
  const storageKey = `hrs:study-canvas:${workspace}:${initial.id}`;
  const [recovery] = useState(() => {
    try {
      return readCanvasBackup(
        localStorage.getItem(storageKey),
        workspace,
        initial.topic_id,
        initial.id,
      );
    } catch {
      return null;
    }
  });
  const differs =
    !!recovery &&
    JSON.stringify(recovery.document) !== JSON.stringify(initial.document);
  const [history, setHistory] = useState(() =>
    createCanvasHistory(differs ? recovery.document : initial.document),
  );
  const historyRef = useRef(history);
  const version = useRef(differs ? recovery.base_version : initial.version);
  const saved = useRef(JSON.stringify(initial.document));
  const [saving, setSaving] = useState(false);
  const inFlight = useRef(false);
  const flushRequested = useRef(false);
  const flushLatest = useRef<() => Promise<boolean>>(async () => false);
  const [error, setError] = useState(
    differs && recovery.base_version !== initial.version
      ? "服务器已有新版本，已保留本地布局。请另存草稿，或核对后载入服务器版本。"
      : "",
  );
  const [conflict, setConflict] = useState(
    differs && recovery.base_version !== initial.version,
  );
  const [notice, setNotice] = useState(
    differs ? "已恢复本机未保存的画布布局。" : "",
  );
  const [backupError, setBackupError] = useState("");
  const [gesture, setGesture] = useState(false);
  const gestureStart = useRef<StudyCanvasDocument | undefined>(undefined);
  const [retainedDraft, setRetainedDraft] = useState<
    StudyCanvasDocument | undefined
  >(() => {
    try {
      return readCanvasBackup(
        localStorage.getItem(`${storageKey}:retained`),
        workspace,
        initial.topic_id,
        initial.id,
      )?.document;
    } catch {
      return undefined;
    }
  });
  const dirty = JSON.stringify(history.present) !== saved.current;

  const retain = useCallback(
    (document: StudyCanvasDocument) => {
      try {
        localStorage.setItem(
          storageKey,
          JSON.stringify({
            workspace_id: workspace,
            topic_id: initial.topic_id,
            canvas_id: initial.id,
            base_version: version.current,
            document,
          }),
        );
        setBackupError("");
      } catch {
        setBackupError(
          "浏览器无法保留恢复副本，请确认保存成功后再离开，或导出当前布局。 ",
        );
      }
    },
    [storageKey, workspace, initial.id, initial.topic_id],
  );

  function apply(next: StudyCanvasDocument, recordHistory = true) {
    const current = historyRef.current;
    const result = recordHistory
      ? commitCanvas(current, next)
      : { ...current, present: next };
    historyRef.current = result;
    setHistory(result);
  }

  function beginGesture() {
    if (!gestureStart.current)
      gestureStart.current = historyRef.current.present;
    setGesture(true);
  }

  function endGesture(next = historyRef.current.present) {
    const before = gestureStart.current;
    gestureStart.current = undefined;
    setGesture(false);
    setHistory((current) => {
      const result = commitCanvas(
        { ...current, present: before || current.present },
        next,
      );
      historyRef.current = result;
      return result;
    });
  }

  const save = useCallback(async () => {
    if (inFlight.current || conflict) return false;
    const document = historyRef.current.present;
    const fingerprint = JSON.stringify(document);
    if (fingerprint === saved.current) {
      setError("");
      return true;
    }
    inFlight.current = true;
    setSaving(true);
    retain(document);
    let acknowledged = false;
    try {
      const { record: result, bases } = await writeCanvasDocument(
        cache,
        storageKey,
        initial.id,
        version.current,
        document,
      );
      version.current = result.version;
      saved.current = fingerprint;
      const current = cache.getQueryData<StudyCanvasRecord>(
        studyKeys.canvas(initial.id),
      );
      if (!current || current.version <= result.version)
        cache.setQueryData(studyKeys.canvas(initial.id), result);
      void cache.invalidateQueries({
        queryKey: studyKeys.canvases(initial.topic_id),
      });
      try {
        const pending = readCanvasBackup(
          localStorage.getItem(storageKey),
          workspace,
          initial.topic_id,
          initial.id,
        );
        if (
          pending &&
          (pending.base_version === result.version ||
            bases.has(pending.base_version))
        ) {
          if (JSON.stringify(pending.document) === fingerprint)
            localStorage.removeItem(storageKey);
          else
            localStorage.setItem(
              storageKey,
              JSON.stringify({ ...pending, base_version: result.version }),
            );
        }
      } catch {
        /* Server acknowledgement never discards an unreadable recovery copy. */
      }
      setError("");
      setNotice("");
      acknowledged = true;
      return true;
    } catch (failure) {
      flushRequested.current = false;
      setError(
        failure instanceof Error
          ? failure.message
          : "保存未完成，当前布局已保留。",
      );
      setConflict(failure instanceof StudyError && failure.status === 409);
      return false;
    } finally {
      inFlight.current = false;
      setSaving(false);
      if (acknowledged && flushRequested.current) {
        flushRequested.current = false;
        const latest = JSON.stringify(historyRef.current.present);
        if (latest !== saved.current) {
          try {
            const pending = readCanvasBackup(
              localStorage.getItem(storageKey),
              workspace,
              initial.topic_id,
              initial.id,
            );
            // A remounted editor may already own a newer draft; never replay this session's older snapshot over it.
            if (!pending || JSON.stringify(pending.document) === latest)
              void flushLatest.current();
          } catch {
            /* Keep the in-memory draft rather than guessing ownership after a storage failure. */
          }
        }
      }
    }
  }, [
    initial.id,
    initial.topic_id,
    storageKey,
    workspace,
    cache,
    retain,
    conflict,
  ]);

  function flush() {
    if (
      !inFlight.current &&
      JSON.stringify(historyRef.current.present) === saved.current
    )
      return Promise.resolve(true);
    retain(historyRef.current.present);
    if (error || conflict || gestureStart.current)
      return Promise.resolve(false);
    flushRequested.current = true;
    return save();
  }
  flushLatest.current = flush;
  const leave = useRef(flush);
  leave.current = flush;
  useEffect(
    () => () => {
      void leave.current();
    },
    [],
  );

  useEffect(() => {
    if (gesture || (!dirty && !saving)) return;
    retain(history.present);
    if (!dirty || saving || error) return;
    const timer = window.setTimeout(() => void save(), 700);
    return () => window.clearTimeout(timer);
  }, [history.present, dirty, gesture, saving, error, retain, save]);

  useEffect(() => {
    if (!dirty && !saving) return;
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty, saving]);

  function undo() {
    setHistory((current) => {
      const result = undoCanvas(current);
      historyRef.current = result;
      return result;
    });
  }
  function redo() {
    setHistory((current) => {
      const result = redoCanvas(current);
      historyRef.current = result;
      return result;
    });
  }

  async function loadServer() {
    try {
      // Retain the outgoing draft until the user can explicitly recover or discard it.
      retain(historyRef.current.present);
      const current = await studies.canvas(initial.id);
      setRetainedDraft(historyRef.current.present);
      try {
        const pending = localStorage.getItem(storageKey);
        if (pending) localStorage.setItem(`${storageKey}:retained`, pending);
        localStorage.removeItem(storageKey);
      } catch {
        /* The retained in-memory draft can still be exported. */
      }
      version.current = current.version;
      saved.current = JSON.stringify(current.document);
      const fresh = createCanvasHistory(current.document);
      historyRef.current = fresh;
      setHistory(fresh);
      cache.setQueryData(studyKeys.canvas(initial.id), current);
      setConflict(false);
      setError("");
      setNotice(
        "已载入服务器版本；此前本机草稿仍保留在恢复副本中，可先导出备份。",
      );
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "无法重新读取服务器版本。",
      );
    }
  }

  async function updateDetails(changes: {
    title?: string;
    archived?: boolean;
  }) {
    if (inFlight.current || !(await save())) return null;
    inFlight.current = true;
    setSaving(true);
    try {
      const result = await studies.updateCanvas(initial.id, {
        expected_version: version.current,
        ...changes,
      });
      version.current = result.version;
      cache.setQueryData(studyKeys.canvas(initial.id), result);
      void cache.invalidateQueries({
        queryKey: studyKeys.canvases(initial.topic_id),
      });
      return result;
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "画布信息未保存。");
      setConflict(failure instanceof StudyError && failure.status === 409);
      return null;
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  }

  return {
    document: history.present,
    apply,
    beginGesture,
    endGesture,
    undo,
    redo,
    canUndo: !!history.past.length,
    canRedo: !!history.future.length,
    save,
    flush,
    loadServer,
    updateDetails,
    retainedDraft,
    dirty,
    saving,
    error,
    conflict,
    notice,
    backupError,
    status: saving
      ? "保存中…"
      : error
        ? "保存未完成"
        : dirty
          ? "尚未保存"
          : "已保存",
  };
}
