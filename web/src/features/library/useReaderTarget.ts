import { useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import type { components } from "@/client/schema";
import { readingRange, requestedReadingTarget } from "./reader-location";

/** URL provenance, source hash and render failures jointly own the visible focus. */
export function useReaderTarget(
  chapter: components["schemas"]["Chapter"] | undefined,
  params: URLSearchParams,
) {
  const location = useLocation();
  const [mappingFailures, setMappingFailures] = useState<string[]>([]);
  // Drawer and query changes preserve the current reading position. Following a
  // source link still gets a fresh navigation key, including repeated hits.
  const focusNavigation = useRef(location.key);
  if (!location.state?.preserveReadingPosition)
    focusNavigation.current = location.key;
  const focusKey = focusNavigation.current;
  const target = useMemo(() => requestedReadingTarget(params), [params]);
  const invalidTarget =
    !target &&
    ["start", "end", "textSha", "run"].some((key) => params.has(key));
  const range = useMemo(
    () => (target && chapter ? readingRange(target, chapter) : null),
    [target, chapter],
  );
  const readingFocus = useMemo(
    () =>
      range
        ? {
            ...range,
            id: `${focusKey}:${target?.run_id}:${target?.chapter_id}:${target?.text_sha256}:${range.start}:${range.end}`,
          }
        : undefined,
    [range, focusKey, target],
  );
  const failedTarget = Boolean(
    chapter &&
    (invalidTarget ||
      (target &&
        (!range ||
          (readingFocus && mappingFailures.includes(readingFocus.id))))),
  );
  const targetSourceChanged = Boolean(
    target &&
    chapter &&
    (target.chapter_id !== chapter.id ||
      target.run_id !== chapter.run_id ||
      target.text_sha256 !== chapter.text_sha256),
  );
  return {
    target,
    readingFocus,
    invalidTarget,
    failedTarget,
    targetSourceChanged,
    mappingFailures,
    setMappingFailures,
  };
}
