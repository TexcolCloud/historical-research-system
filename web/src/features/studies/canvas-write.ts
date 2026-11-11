import type { QueryClient } from "@tanstack/react-query";
import { studies, studyKeys } from "./api";
import type { StudyCanvasDocument, StudyCanvasRecord } from "./types";

type CanvasWrite = {
  bases: Set<number>;
  acknowledgedVersion: Promise<number>;
};
const writes = new WeakMap<QueryClient, Map<string, CanvasWrite>>();

/** Carry acknowledged versions across editor remounts; failed outcomes never trigger a queued retry. */
export function writeCanvasDocument(
  cache: QueryClient,
  scope: string,
  canvasId: string,
  expectedVersion: number,
  document: StudyCanvasDocument,
) {
  let workspaceWrites = writes.get(cache);
  if (!workspaceWrites) {
    workspaceWrites = new Map();
    writes.set(cache, workspaceWrites);
  }
  const previous = workspaceWrites.get(scope);
  const predecessor = previous?.bases.has(expectedVersion)
    ? previous
    : undefined;
  const bases = new Set([expectedVersion, ...(predecessor?.bases || [])]);
  const result = (async () => {
    let version = expectedVersion;
    if (predecessor) {
      version = await predecessor.acknowledgedVersion;
      bases.add(version);
      const acknowledged = cache.getQueryData<StudyCanvasRecord>(
        studyKeys.canvas(canvasId),
      );
      if (
        acknowledged?.version === version &&
        JSON.stringify(acknowledged.document) === JSON.stringify(document)
      )
        return acknowledged;
    }
    return studies.updateCanvas(canvasId, {
      expected_version: version,
      document,
    });
  })();
  const entry = {
    bases,
    acknowledgedVersion: result.then((record) => record.version),
  };
  workspaceWrites.set(scope, entry);
  void entry.acknowledgedVersion.catch(() => {
    if (workspaceWrites.get(scope) === entry) workspaceWrites.delete(scope);
  });
  return result.then((record) => ({ record, bases }));
}
