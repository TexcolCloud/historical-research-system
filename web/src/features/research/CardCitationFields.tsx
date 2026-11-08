import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { client } from "@/client/api";
import type { components } from "@/client/schema";
import { research, researchKeys, type ResearchCard } from "./api";
import { References, certainty, newId, type Draft } from "./CardFields";
import { crossRelationLabels } from "./relation-labels";
type S = components["schemas"];
function EvidenceText({
  value,
  chapterId,
  expectedSha,
  onChange,
}: {
  value: Draft["evidence"][number];
  chapterId?: string;
  expectedSha?: string;
  onChange: (value: Draft["evidence"][number]) => void;
}) {
  const [open, setOpen] = useState(false),
    [selection, setSelection] = useState<{ start: number; end: number }>();
  const original = useQuery({
    queryKey: ["research", "citation-source", chapterId, expectedSha],
    enabled: open && Boolean(chapterId),
    staleTime: Infinity,
    queryFn: async ({ signal }) => {
      const { data } = await client.GET("/api/v2/chapters/{chapter_id}", {
        params: { path: { chapter_id: chapterId! } },
        signal,
      });
      if (!data) throw new Error("原文暂时无法读取。");
      if (expectedSha && data.text_sha256 !== expectedSha)
        throw new Error(
          "原文版本已有变化，当前修订草稿保留。请先核对原卡的来源版本。",
        );
      return data;
    },
  });
  return (
    <div>
      <label>
        引文
        <textarea
          aria-label="证据原文"
          rows={4}
          value={value.quote}
          onChange={(event) =>
            onChange({ ...value, quote: event.target.value })
          }
        />
      </label>
      <p className="research-provenance">
        修改引文后，应从完整原文重新选取对应文字。原文位置不能凭措辞推测。
      </p>
      {chapterId && (
        <Button type="button" variant="outline" onClick={() => setOpen(!open)}>
          {open ? "收起原文选取" : "从完整原文选取"}
        </Button>
      )}
      {open && (
        <div>
          {original.isPending ? (
            <p role="status">正在读取完整原文…</p>
          ) : original.isError ? (
            <p role="alert">{original.error.message}</p>
          ) : (
            original.data && (
              <>
                <label>
                  完整原文（选择需要引用的文字）
                  <textarea
                    aria-label="完整原文选取"
                    className="research-source-transcript"
                    readOnly
                    rows={12}
                    value={original.data.text}
                    onSelect={(event) => {
                      const el = event.currentTarget;
                      setSelection({
                        start: Array.from(el.value.slice(0, el.selectionStart))
                          .length,
                        end: Array.from(el.value.slice(0, el.selectionEnd))
                          .length,
                      });
                    }}
                  />
                </label>
                <Button
                  type="button"
                  disabled={!selection || selection.start === selection.end}
                  onClick={() => {
                    if (selection && original.data)
                      onChange({
                        ...value,
                        start: selection.start,
                        end: selection.end,
                        quote: Array.from(original.data.text)
                          .slice(selection.start, selection.end)
                          .join(""),
                      });
                  }}
                >
                  使用所选原文
                </Button>
              </>
            )
          )}
        </div>
      )}
      <p className="research-provenance">
        原文范围：{value.start}–{value.end} 字符
      </p>
    </div>
  );
}

function TargetCardPicker({
  onSelect,
}: {
  onSelect: (value: ResearchCard) => void;
}) {
  const [text, setText] = useState(""),
    [query, setQuery] = useState("");
  const result = useQuery({
    queryKey: ["research", "target-card", query],
    queryFn: ({ signal }) => research.search(query, [], signal),
    enabled: Boolean(query),
    staleTime: 30000,
  });
  const choose = useMutation({
    mutationFn: (id: string) => research.card(id),
    onSuccess: (card) => {
      if (
        card.source_current &&
        !card.superseded &&
        ["verified", "human_accepted"].includes(card.state)
      )
        onSelect(card);
    },
  });
  return (
    <div className="research-target-picker">
      <label>
        查找对方史料卡
        <Input
          aria-label="查找对方史料卡"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      <Button
        type="button"
        variant="outline"
        disabled={!text.trim()}
        onClick={() => setQuery(text.trim())}
      >
        查找目标卡片
      </Button>
      {result.isFetching && <p role="status">正在查找卡片…</p>}
      {result.error && <p role="alert">{result.error.message}</p>}
      {result.data?.cards
        .filter((card) => ["verified", "human_accepted"].includes(card.state))
        .map((card) => (
          <Button
            type="button"
            key={card.id}
            variant="ghost"
            disabled={choose.isPending}
            onClick={() => choose.mutate(card.id)}
          >
            {card.title}
          </Button>
        ))}
      {choose.error && <p role="alert">{choose.error.message}</p>}
    </div>
  );
}
function CrossRelationEditor({
  value,
  evidence,
  onChange,
  onRemove,
}: {
  value: S["CrossCardRelation"];
  evidence: Draft["evidence"];
  onChange: (value: S["CrossCardRelation"]) => void;
  onRemove: () => void;
}) {
  const target = useQuery({
    queryKey: researchKeys.card(value.target_card_id),
    queryFn: ({ signal }) => research.card(value.target_card_id, signal),
    staleTime: 30000,
  });
  return (
    <fieldset className="research-edit-group">
      <legend>{target.data?.title ?? "跨卡关系"}</legend>
      <label>
        关系类型
        <select
          value={value.kind}
          onChange={(event) =>
            onChange({
              ...value,
              kind: event.target.value as typeof value.kind,
            })
          }
        >
          {Object.entries(crossRelationLabels).map(([key, text]) => (
            <option key={key} value={key}>
              {text}
            </option>
          ))}
        </select>
      </label>
      <label>
        关系依据
        <textarea
          value={value.reason}
          onChange={(event) =>
            onChange({ ...value, reason: event.target.value })
          }
          rows={3}
          required
        />
      </label>
      <label>
        判断状态
        <select
          value={value.certainty}
          onChange={(event) =>
            onChange({
              ...value,
              certainty: event.target.value as typeof value.certainty,
            })
          }
        >
          {Object.entries(certainty)
            .filter(([key]) => key !== "unknown")
            .map(([key, text]) => (
              <option key={key} value={key}>
                {text}
              </option>
            ))}
        </select>
      </label>
      <References
        label="本卡依据"
        value={value.source_evidence_ids}
        evidence={evidence}
        onChange={(source_evidence_ids) =>
          onChange({ ...value, source_evidence_ids })
        }
      />
      {target.data && (
        <>
          <Link
            to={`/research?card=${encodeURIComponent(target.data.id)}`}
            target="_blank"
          >
            核对对方卡片与原页
          </Link>
          <References
            label="对方卡片依据"
            value={value.target_evidence_ids}
            evidence={target.data.evidence}
            onChange={(target_evidence_ids) =>
              onChange({ ...value, target_evidence_ids })
            }
          />
        </>
      )}
      {target.isError && <p role="alert">{target.error.message}</p>}
      <Button type="button" variant="ghost" onClick={onRemove}>
        移除此关系
      </Button>
    </fieldset>
  );
}

export function CitationFields({
  value,
  onChange,
  card,
}: {
  value: Draft["evidence"];
  onChange: (value: Draft["evidence"]) => void;
  card?: ResearchCard;
}) {
  return (
    <details>
      <summary>引文与原文范围</summary>
      {value.map((item, index) => (
        <fieldset className="research-edit-group" key={item.id}>
          <legend>证据 {index + 1}</legend>
          <EvidenceText
            value={item}
            chapterId={
              card?.evidence.find((ref) => ref.id === item.id)?.reading_target
                ?.chapter_id
            }
            expectedSha={
              card?.evidence.find((ref) => ref.id === item.id)?.reading_target
                ?.text_sha256
            }
            onChange={(next) =>
              onChange(value.map((old, at) => (at === index ? next : old)))
            }
          />
          <label>
            注释标识（每行一项）
            <textarea
              value={(item.footnote_ids ?? []).join("\n")}
              onChange={(event) =>
                onChange(
                  value.map((old, at) =>
                    at === index
                      ? {
                          ...old,
                          footnote_ids: event.target.value
                            .split("\n")
                            .map((line) => line.trim())
                            .filter(Boolean),
                        }
                      : old,
                  ),
                )
              }
            />
          </label>
        </fieldset>
      ))}
    </details>
  );
}
export function CrossRelationFields({
  value,
  evidence,
  onChange,
}: {
  value: NonNullable<Draft["cross_relations"]>;
  evidence: Draft["evidence"];
  onChange: (value: NonNullable<Draft["cross_relations"]>) => void;
}) {
  return (
    <details>
      <summary>跨卡文书及证据关系</summary>
      {value.map((item, index) => (
        <CrossRelationEditor
          key={item.id}
          value={item}
          evidence={evidence}
          onChange={(next) =>
            onChange(value.map((old, at) => (at === index ? next : old)))
          }
          onRemove={() => onChange(value.filter((_, at) => at !== index))}
        />
      ))}
      <TargetCardPicker
        onSelect={(target) => {
          if (typeof target.publication_sha256 !== "string") return;
          onChange([
            ...value,
            {
              id: newId("cross"),
              target_card_id: target.id,
              target_card_version: target.version,
              target_publication_sha256: target.publication_sha256,
              kind: "association",
              reason: "",
              certainty: "inferred",
              source_evidence_ids: [],
              target_evidence_ids: [],
            },
          ]);
        }}
      />
    </details>
  );
}
