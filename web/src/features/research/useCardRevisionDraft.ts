import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import type { Draft } from "./CardFields";

type RetainedDraft = {
  schema: 1;
  workspaceId: string;
  cardId: string;
  baseVersion: number;
  draft: Draft;
};

// Storage is untrusted input. Check the editable structure without defaulting,
// stripping, or normalizing user content; business validation stays on submit.
const strings = z.array(z.string());
const sourceValue = z.looseObject({
  value: z.string(),
  certainty: z.string(),
  evidence_ids: strings,
  note: z.string(),
});
const date = z.looseObject({
  text: z.string(),
  certainty: z.string(),
  normalized: z.string().nullable().optional(),
  evidence_ids: strings.optional(),
});
const identified = z.looseObject({ id: z.string() });
const located = z.looseObject({
  article_id: z.string(),
  start: z.number(),
  end: z.number(),
  quote: z.string(),
});
const named = identified.extend({
  name: z.string(),
  evidence_ids: strings,
});
const cachedDraftShape = z.looseObject({
  id: z.string(),
  article_id: z.string(),
  title: z.string(),
  summary: z.string(),
  document_type: z.string(),
  source_layer: z.string(),
  formation_date: date.optional(),
  event_date: date.optional(),
  evidence: z.array(
    located.extend({ id: z.string(), footnote_ids: strings.optional() }),
  ),
  items: z
    .array(
      identified.extend({
        title: z.string(),
        text: z.string(),
        evidence_ids: strings,
        limitations: strings.optional(),
      }),
    )
    .optional(),
  mentions: z
    .array(
      named.extend({
        locations: z.array(located).optional(),
        aliases: z
          .array(z.looseObject({ name: z.string(), evidence_ids: strings }))
          .optional(),
      }),
    )
    .optional(),
  events: z
    .array(
      named.extend({
        date: date.optional(),
        participant_ids: strings.optional(),
      }),
    )
    .optional(),
  relations: z
    .array(
      identified.extend({
        subject_id: z.string(),
        object_id: z.string(),
        predicate: z.string(),
        evidence_ids: strings,
      }),
    )
    .optional(),
  identity_links: z
    .array(
      z.looseObject({ local_mention_id: z.string(), entity_id: z.string() }),
    )
    .optional(),
  cross_relations: z
    .array(
      identified.extend({
        target_card_id: z.string(),
        source_evidence_ids: strings,
        target_evidence_ids: strings,
      }),
    )
    .optional(),
  tags: strings.optional(),
  limitations: strings.optional(),
  alternatives: strings.optional(),
  questions: strings.optional(),
  coverage: z
    .array(
      identified.extend({
        mention_ids: strings.optional(),
        event_ids: strings.optional(),
        relation_ids: strings.optional(),
      }),
    )
    .optional(),
  metadata: z
    .object({ title_origin: z.string() })
    .catchall(sourceValue)
    .nullable()
    .optional(),
  dossier: z
    .looseObject({
      research_scope: sourceValue,
      formation_background: sourceValue,
      text_structure: sourceValue,
      source_nature: sourceValue,
      source_role: sourceValue,
      research_value: sourceValue,
      can_support: sourceValue,
      cannot_support: sourceValue,
      needs_corroboration: sourceValue,
      argument_units: z.array(
        identified.extend({
          title: z.string(),
          claim: z.string(),
          evidence_ids: strings,
          applicability_limit: z.string(),
          corroboration_needed: z.string(),
        }),
      ),
      argument_units_note: z.string().optional(),
      citation: z.looseObject({
        full: sourceValue,
        short: sourceValue,
        printed_pages: sourceValue,
        publication_date: date,
        completeness: z.string(),
        missing_fields: strings,
      }),
    })
    .optional(),
});
const retainedShape = z.object({
  schema: z.literal(1),
  workspaceId: z.string(),
  cardId: z.string(),
  baseVersion: z.number().int().positive(),
  draft: z.custom<Draft>((value) => cachedDraftShape.safeParse(value).success),
});

function checkedDraft(
  value: unknown,
  initial: Draft,
  cardId: string,
  workspaceId: string,
): RetainedDraft | undefined {
  const parsed = retainedShape.safeParse(value);
  if (!parsed.success) return;
  const saved = parsed.data;
  if (
    saved.workspaceId === workspaceId &&
    saved.cardId === cardId &&
    saved.draft.id === initial.id &&
    saved.draft.article_id === initial.article_id
  )
    return saved;
}

function retainedDraft(
  key: string | undefined,
  initial: Draft,
  cardId: string,
  workspaceId?: string,
) {
  if (!key) return;
  try {
    return checkedDraft(
      JSON.parse(localStorage.getItem(key) || "null"),
      initial,
      cardId,
      workspaceId ?? "unconfirmed",
    );
  } catch {
    /* A damaged local copy never replaces the source-owned editable card. */
  }
}

/** A local revision is bound to its workspace and original CAS version, never an approval. */
export function useCardRevisionDraft(
  initial: Draft,
  version: number,
  cardId: string,
  workspaceId?: string,
) {
  const cache = useQueryClient();
  const [cacheKey] = useState(() => [
    "research",
    "revision-draft",
    workspaceId ?? "unconfirmed",
    cardId,
  ]);
  const [key] = useState(() =>
    workspaceId
      ? `hrs:card-revision:${encodeURIComponent(workspaceId)}:${encodeURIComponent(cardId)}`
      : undefined,
  );
  const [retained] = useState(() => {
    const memory = checkedDraft(
      cache.getQueryData<unknown>(cacheKey),
      initial,
      cardId,
      workspaceId ?? "unconfirmed",
    );
    if (memory) return memory;
    return retainedDraft(key, initial, cardId, workspaceId);
  });
  const [draft, setDraft] = useState<Draft>(() =>
    structuredClone(retained?.draft ?? initial),
  );
  const [baseVersion, setBaseVersion] = useState(
    retained?.baseVersion ?? version,
  );
  const [confirmedVersion, setConfirmedVersion] = useState(version);
  const [storageError, setStorageError] = useState(false);
  const [stored, setStored] = useState(Boolean(retained));
  const committed = useRef(false);
  const current = useRef({ draft, baseVersion, dirty: false });
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(initial) ||
    baseVersion !== version;
  current.current = { draft, baseVersion, dirty };

  function persist() {
    if (committed.current) return false;
    const saved = {
      schema: 1 as const,
      workspaceId: workspaceId ?? "unconfirmed",
      cardId,
      baseVersion: current.current.baseVersion,
      draft: current.current.draft,
    };
    cache.setQueryData(cacheKey, current.current.dirty ? saved : null);
    if (!key || !workspaceId) return false;
    try {
      if (current.current.dirty) {
        localStorage.setItem(key, JSON.stringify(saved));
      } else localStorage.removeItem(key);
      return true;
    } catch {
      return false;
    }
  }

  useEffect(() => {
    cache.setQueryDefaults(cacheKey, { gcTime: Infinity });
    if (!dirty || committed.current) return;
    setStored(false);
    const timer = window.setTimeout(() => {
      const success = persist();
      setStorageError(Boolean(key && !success));
      setStored(success);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [draft, baseVersion, dirty, key]);
  useEffect(() => {
    const leaving = (event: BeforeUnloadEvent) => {
      persist();
      if (current.current.dirty && !committed.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", leaving);
    return () => {
      window.removeEventListener("beforeunload", leaving);
      persist();
    };
  }, [key]);

  function submitted() {
    committed.current = true;
    cache.setQueryData(cacheKey, null);
    if (key) {
      try {
        localStorage.removeItem(key);
      } catch {
        setStorageError(true);
      }
    }
  }
  function discard() {
    if (key) {
      try {
        localStorage.removeItem(key);
      } catch {
        setStorageError(true);
        return;
      }
    }
    cache.setQueryData(cacheKey, null);
    setDraft(structuredClone(initial));
    setBaseVersion(version);
    setConfirmedVersion(version);
    setStored(false);
  }
  return {
    draft,
    setDraft,
    baseVersion,
    setBaseVersion: (next: number) => {
      setBaseVersion(next);
      setConfirmedVersion(next);
    },
    dirty,
    restored: Boolean(retained),
    versionConflict: baseVersion !== confirmedVersion,
    stored,
    storageError,
    persistent: Boolean(key),
    persist,
    submitted,
    discard,
  };
}
