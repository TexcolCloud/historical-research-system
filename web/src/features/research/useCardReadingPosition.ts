import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { cardChapters } from "./CardDossierSections";

/** The URL carries the reading scene; locations from another card version are ignored. */
export function useCardReadingPosition(
  cardId: string,
  version: number,
  scope: string,
  enabled: boolean,
) {
  const [params, setParams] = useSearchParams();
  const bound =
    params.get("cardReadId") === cardId &&
    params.get("cardReadVersion") === String(version);
  const chapter =
    bound && cardChapters.some(([id]) => id === params.get("cardChapter"))
      ? params.get("cardChapter")!
      : "basic";
  const selected = bound
    ? (params.get("cardEvidence") ?? undefined)
    : undefined;
  const latest = useRef({ params, setParams, chapter, selected });
  latest.current = { params, setParams, chapter, selected };

  function change(values: Record<string, string | undefined>) {
    const next = new URLSearchParams(latest.current.params);
    next.set("cardReadId", cardId);
    next.set("cardReadVersion", String(version));
    for (const [key, value] of Object.entries(values)) {
      if (value == null) next.delete(key);
      else next.set(key, value);
    }
    latest.current.setParams(next, { replace: true });
  }
  useEffect(() => {
    if (!enabled) return;
    const current = document.getElementById(
      `${scope}-${latest.current.chapter}`,
    );
    if (
      latest.current.params.get("cardReadId") === cardId &&
      latest.current.params.get("cardReadVersion") === String(version)
    )
      current?.scrollIntoView?.({ block: "start" });
    if (typeof IntersectionObserver === "undefined") return;
    const visible = new Map<Element, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        // The drawer can scroll and reflow the document while looking up a quote.
        // Keep its originating chapter as the return anchor until it is closed.
        if (latest.current.selected) {
          visible.clear();
          return;
        }
        for (const entry of entries) {
          if (entry.isIntersecting)
            visible.set(entry.target, Math.abs(entry.boundingClientRect.top));
          else visible.delete(entry.target);
        }
        const target = [...visible].sort((a, b) => a[1] - b[1])[0]?.[0];
        const id = target?.getAttribute("data-card-chapter");
        if (id && id !== latest.current.chapter) change({ cardChapter: id });
      },
      { rootMargin: "-110px 0px -55% 0px", threshold: 0 },
    );
    for (const [id] of cardChapters) {
      const element = document.getElementById(`${scope}-${id}`);
      if (element) observer.observe(element);
    }
    return () => observer.disconnect();
  }, [cardId, version, scope, enabled]);

  return {
    activeChapter: chapter,
    selected,
    selectChapter(id: string) {
      change({ cardChapter: id });
      document
        .getElementById(`${scope}-${id}`)
        ?.scrollIntoView?.({ block: "start" });
    },
    selectEvidence(id?: string, originChapter?: string) {
      change({
        cardChapter: cardChapters.some(([id]) => id === originChapter)
          ? originChapter
          : latest.current.chapter,
        cardEvidence: id,
      });
    },
  };
}
