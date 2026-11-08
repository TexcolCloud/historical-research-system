import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { readingHref } from "@/features/library/reader-location";
import type { components } from "@/client/schema";
import { research, researchKeys, type ResearchEntity } from "./api";
import { useResearchPendingNavigation } from "./pending-navigation";

type Ref = components["schemas"]["ResearchEvidenceRefResponse"];
type SourceMention = components["schemas"]["ResearchSourceMentionResponse"];
type Action = components["schemas"]["IdentityDecision"]["action"];
const actions: Record<Action, string> = {
  merge: "确认同一身份",
  distinct: "确认不同身份",
  split: "拆分记载",
  alias: "记录别名",
  alias_remove: "移除别名",
};
const refKey = (ref: Ref) =>
  `${ref.card_id}/${ref.card_version}/${ref.evidence_id}`;
const maxEvidenceRefs = 100;
function refsFor(entity: ResearchEntity) {
  return [
    ...new Map(
      entity.source_mentions
        .filter((member) => member.source_current !== false)
        .flatMap((member) => member.evidence_refs ?? [])
        .filter(
          (ref) =>
            Number.isSafeInteger(ref.card_version) && ref.card_version > 0,
        )
        .map((ref) => [refKey(ref), ref]),
    ).values(),
  ];
}
function defaultProof(source: ResearchEntity, target?: ResearchEntity) {
  const groups = [refsFor(source), ...(target ? [refsFor(target)] : [])];
  // Keep proof from both identities before filling the bounded request.
  const ordered = [
    ...groups.flatMap((group) => group.slice(0, 1)),
    ...groups.flatMap((group) => group.slice(1)),
  ];
  return [...new Set(ordered.map(refKey))].slice(0, maxEvidenceRefs);
}
function SourceChoices({
  label,
  members,
  selected,
  onChange,
}: {
  label: string;
  members: SourceMention[];
  selected: string[];
  onChange: (keys: string[]) => void;
}) {
  const seen = new Set<string>();
  return (
    <fieldset className="research-reference-selector">
      <legend>{label}</legend>
      {members.flatMap((member) =>
        (member.evidence_refs ?? []).flatMap((ref) => {
          const key = refKey(ref);
          if (seen.has(key)) return [];
          seen.add(key);
          const evidence =
            ref.evidence ??
            member.evidence?.find((item) => item.id === ref.evidence_id);
          return [
            <div key={key}>
              <label>
                <input
                  type="checkbox"
                  disabled={
                    member.source_current === false ||
                    (selected.length >= maxEvidenceRefs &&
                      !selected.includes(key))
                  }
                  checked={selected.includes(key)}
                  onChange={(event) =>
                    onChange(
                      event.target.checked
                        ? [...selected, key]
                        : selected.filter((value) => value !== key),
                    )
                  }
                />
                <span>
                  {label} · {member.card_title || member.name}：
                  {evidence?.quote ?? "保留的原文证据"}
                </span>
              </label>
              {evidence?.reading_target && (
                <Link
                  target="_blank"
                  to={readingHref(
                    evidence.book_id || member.book_id,
                    evidence.reading_target,
                  )}
                >
                  打开原页核对
                </Link>
              )}
            </div>,
          ];
        }),
      )}
    </fieldset>
  );
}

function IdentityForm({
  entity,
  onClose,
  onSaved,
}: {
  entity: ResearchEntity;
  onClose: () => void;
  onSaved?: (value: ResearchEntity) => void;
}) {
  const [params] = useSearchParams();
  const pendingNavigation = useResearchPendingNavigation();
  const query = useQueryClient();
  const [source, setSource] = useState(entity),
    [target, setTarget] = useState<ResearchEntity>(),
    [action, setAction] = useState<Action>("merge"),
    [selected, setSelected] = useState<string[]>(() => defaultProof(entity)),
    [members, setMembers] = useState<string[]>([]),
    [alias, setAlias] = useState(""),
    [reason, setReason] = useState("");
  const [text, setText] = useState(""),
    [lookup, setLookup] = useState("");
  const candidates = useQuery({
    queryKey: ["research", "identity-candidates", source.kind, lookup],
    queryFn: ({ signal }) => research.catalogue(lookup, source.kind, signal),
    enabled: Boolean(lookup),
    staleTime: 30000,
  });
  const choose = useMutation({
    mutationFn: (id: string) => research.entity(id),
    onSuccess: (saved) => {
      setTarget(saved);
      setSelected(defaultProof(source, saved));
    },
  });
  const initialTarget = params.get("compare");
  useEffect(() => {
    if (initialTarget) choose.mutate(initialTarget);
  }, [initialTarget, choose.mutate]);
  const reload = useMutation({
    mutationFn: async () => ({
      source: await research.entity(source.id),
      target: target ? await research.entity(target.id) : undefined,
    }),
    onSuccess: (saved) => {
      setSource(saved.source);
      setTarget(saved.target);
      setSelected(defaultProof(saved.source, saved.target));
      setMembers([]);
      decision.reset();
    },
  });
  const currentRefs = refsFor(source),
    targetRefs = target ? refsFor(target) : [];
  const allRefs = [
    ...new Map(
      [...currentRefs, ...targetRefs].map((ref) => [refKey(ref), ref]),
    ).values(),
  ];
  const selectedRefs = allRefs.filter((ref) => selected.includes(refKey(ref)));
  const pair = action === "merge" || action === "distinct";
  const aliasAction = action === "alias" || action === "alias_remove";
  const currentProof = currentRefs.some((ref) =>
      selected.includes(refKey(ref)),
    ),
    targetProof = targetRefs.some((ref) => selected.includes(refKey(ref)));
  const canSave =
    currentProof &&
    (!pair || (target && target.id !== source.id && targetProof)) &&
    (!aliasAction || Boolean(alias.trim())) &&
    (action !== "alias_remove" ||
      source.aliases.some((item) => item.name === alias)) &&
    (action !== "split" ||
      (members.length > 0 && members.length < source.source_mentions.length));
  const decision = useMutation({
    mutationFn: () =>
      research.identityDecision(source.id, {
        action,
        expected_version: source.version ?? 1,
        ...(pair && target
          ? { target_id: target.id, target_version: target.version ?? 1 }
          : {}),
        ...(action === "split" ? { mention_ids: members } : {}),
        ...(aliasAction ? { alias: alias.trim() } : {}),
        evidence_refs: selectedRefs.map((ref) => ({
          card_id: ref.card_id,
          card_version: ref.card_version,
          evidence_id: ref.evidence_id,
        })),
        reason: reason.trim(),
      }),
    onSuccess: (saved) => {
      query.setQueryData(researchKeys.entity(saved.id), saved);
      void query.invalidateQueries({ queryKey: ["research"] });
      onSaved?.(saved);
      void pendingNavigation.advance({ entityId: source.id });
    },
  });
  const busy = decision.isPending || reload.isPending || choose.isPending;
  return (
    <section className="research-identity-editor" aria-label="身份核对">
      <header>
        <h3>核对身份与来源记载</h3>
        <Button variant="ghost" disabled={decision.isPending} onClick={onClose}>
          返回条目
        </Button>
      </header>
      <p>
        同名不等于同一身份。下方默认选中当前来源依据，最多100条，可取消后调整选择。对照原文后保存最终判断，已有记录与决定会保留。
      </p>
      <div className="research-decision-actions">
        {Object.entries(actions).map(([key, label]) => (
          <Button
            key={key}
            variant={action === key ? "default" : "outline"}
            disabled={busy}
            aria-pressed={action === key}
            onClick={() => {
              setAction(key as Action);
              setSelected(
                defaultProof(
                  source,
                  ["merge", "distinct"].includes(key) ? target : undefined,
                ),
              );
              decision.reset();
            }}
          >
            {label}
          </Button>
        ))}
      </div>
      <div className="research-identity-pair">
        <section>
          <h4>当前条目：{source.name}</h4>
          <SourceChoices
            label="本条目依据"
            members={source.source_mentions}
            selected={selected}
            onChange={setSelected}
          />
        </section>
        {pair && (
          <section>
            <h4>比较对象</h4>
            <label>
              查找身份
              <Input
                aria-label="查找需要比较的身份"
                value={text}
                onChange={(event) => setText(event.target.value)}
              />
            </label>
            <Button
              variant="outline"
              disabled={!text.trim() || busy}
              onClick={() => setLookup(text.trim())}
            >
              查找身份
            </Button>
            {candidates.data?.items
              .filter(
                (item) =>
                  (item.canonical_id ?? item.id) !==
                  (source.canonical_id ?? source.id),
              )
              .map((item) => (
                <Button
                  variant="ghost"
                  key={item.id}
                  onClick={() => choose.mutate(item.id)}
                  disabled={busy}
                >
                  {item.name} · {item.card_title || "查看来源记载"}
                </Button>
              ))}
            {target && (
              <>
                <strong>{target.name}</strong>
                <SourceChoices
                  label="比较对象依据"
                  members={target.source_mentions}
                  selected={selected}
                  onChange={setSelected}
                />
              </>
            )}
            {candidates.isError && (
              <p role="alert">{candidates.error.message}</p>
            )}
            {choose.isError && <p role="alert">{choose.error.message}</p>}
          </section>
        )}
      </div>
      {action === "alias_remove" && (
        <label>
          需要移除的别名
          <select
            aria-label="需要移除的别名"
            value={alias}
            onChange={(event) => setAlias(event.target.value)}
          >
            <option value="">选择当前别名</option>
            {source.aliases.map((item) => (
              <option value={item.name} key={item.name}>
                {item.name}
              </option>
            ))}
          </select>
          <span className="research-provenance">
            移除后保留决定与旧版本，后续模型不能自行恢复这项人工撤回。
          </span>
        </label>
      )}
      {action === "alias" && (
        <label>
          来源别名
          <Input
            aria-label="来源别名"
            value={alias}
            maxLength={200}
            onChange={(event) => setAlias(event.target.value)}
          />
        </label>
      )}
      {action === "split" && (
        <fieldset className="research-reference-selector">
          <legend>选择需要单独分离的记载</legend>
          {source.source_mentions.map((member) => (
            <label key={member.id}>
              <input
                type="checkbox"
                checked={members.includes(member.id)}
                onChange={(event) =>
                  setMembers(
                    event.target.checked
                      ? [...members, member.id]
                      : members.filter((value) => value !== member.id),
                  )
                }
              />
              <span>
                {member.card_title || member.name} · {member.identity_basis}
              </span>
            </label>
          ))}
          <p>至少选择一处，并为原身份保留其他记载。</p>
        </fieldset>
      )}
      <label>
        补充说明（可选）
        <textarea
          aria-label="身份判断说明"
          value={reason}
          rows={3}
          maxLength={2000}
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
      {!currentRefs.length && (
        <p className="workspace-status-note">
          当前条目没有可供提交的完整版本依据，请更新研究资料后再处理。
        </p>
      )}
      <Button
        disabled={!canSave || busy || decision.isSuccess}
        onClick={() => decision.mutate()}
      >
        确认并保存身份决定
      </Button>
      {decision.isPending && <p role="status">正在保存身份决定…</p>}
      {decision.isSuccess && (
        <p role="status">身份决定已保存，原有来源和判断继续保留。</p>
      )}
      {decision.isError && <p role="alert">{decision.error.message}</p>}
      {pendingNavigation.notice && (
        <p role="status">{pendingNavigation.notice}</p>
      )}
      {decision.isError && (
        <>
          <p>
            你的输入仍保留。重新读取后，请对照最新版本及所选来源依据再确认。
          </p>
          <Button
            variant="outline"
            onClick={() => reload.mutate()}
            disabled={reload.isPending}
          >
            重新核对最新记录
          </Button>
        </>
      )}
      {reload.isError && <p role="alert">{reload.error.message}</p>}
    </section>
  );
}
export default function IdentityEditor({
  entityId,
  onClose,
  onSaved,
}: {
  entityId: string;
  onClose: () => void;
  onSaved?: (value: ResearchEntity) => void;
}) {
  const entity = useQuery({
    queryKey: researchKeys.entity(entityId),
    queryFn: ({ signal }) => research.entity(entityId, signal),
    staleTime: 30000,
    retry: false,
  });
  if (entity.isPending) return <p role="status">正在读取身份与来源…</p>;
  if (entity.isError)
    return (
      <p role="alert">
        {entity.error.message}
        <Button onClick={() => void entity.refetch()}>重新读取身份</Button>
      </p>
    );
  if (
    !entity.data.canonical_id ||
    entity.data.source_current === false ||
    entity.data.research_current === false
  )
    return (
      <p className="workspace-status-note">
        请在当前已核验的稳定身份条目中处理。
        <Button onClick={onClose}>返回条目</Button>
      </p>
    );
  return (
    <IdentityForm entity={entity.data} onClose={onClose} onSaved={onSaved} />
  );
}
