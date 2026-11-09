import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { components } from "@/client/schema";
import {
  DateEditor,
  DeferredFields,
  References,
  SourceEditor,
  newId,
  type Draft,
} from "./CardFields";

type Dossier = components["schemas"]["CardDossier"];
type SourceValue = components["schemas"]["SourceValue"];
const sourceLabels = {
  research_scope: "研究主题与范围",
  formation_background: "形成背景",
  text_structure: "文本结构",
  source_nature: "史料性质",
  source_role: "史料在论证中的作用",
  research_value: "研究价值",
  can_support: "能够支持什么",
  cannot_support: "不能据此支持什么",
  needs_corroboration: "仍需互证的事项",
} as const;
const citationLabels = {
  full: "完整引注",
  short: "简短引注",
  printed_pages: "印刷页码",
} as const;
const unknownHint =
  "未详可保持未明；说明缺失或不确定之处，不补写没有依据的事实。";
const lines = (text: string) =>
  text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

type DossierIssue = { message: string; keys: string[]; label: string };
export function dossierValidation(
  value: Dossier,
  evidence: Draft["evidence"],
): DossierIssue | undefined {
  const available = new Set(evidence.map((span) => span.id));
  function sourceIssue(
    source: SourceValue,
    label: string,
    keys: string[],
  ): DossierIssue | undefined {
    if (source.certainty === "unknown" && source.value.trim())
      return {
        label,
        keys,
        message: `${label}仍标为未明，请清空事实值或核对记载状态。`,
      };
    if (
      source.certainty !== "unknown" &&
      (!source.value.trim() || !source.evidence_ids?.length)
    )
      return {
        label,
        keys,
        message: `${label}需要记载内容和本卡原文依据；缺少依据时可保留未明。`,
      };
    if (source.evidence_ids?.some((id) => !available.has(id)))
      return {
        label,
        keys,
        message: `${label}引用的依据已不在本卡，请重新选取。`,
      };
  }
  for (const key of Object.keys(sourceLabels) as Array<
    keyof typeof sourceLabels
  >) {
    const issue = sourceIssue(value[key], sourceLabels[key], [
      "dossier",
      `dossier:${key}`,
    ]);
    if (issue) return issue;
  }
  if (!value.argument_units.length && !value.argument_units_note?.trim())
    return {
      label: "未列论证单元说明",
      keys: ["dossier-units"],
      message: "未列论证单元时，请说明这份来源的适用限制。",
    };
  for (const [index, unit] of value.argument_units.entries()) {
    const label = `论证单元 ${index + 1}`;
    const keys = ["dossier-units", `dossier-unit:${unit.id}`];
    if (
      !unit.title.trim() ||
      !unit.claim.trim() ||
      !unit.applicability_limit.trim() ||
      !unit.evidence_ids.length
    )
      return {
        label,
        keys,
        message: `${label}需要标题、论点、适用边界和本卡原文依据。`,
      };
    if (unit.evidence_ids.some((id) => !available.has(id)))
      return {
        label,
        keys,
        message: `${label}引用的依据已不在本卡，请重新选取。`,
      };
  }
  for (const key of Object.keys(citationLabels) as Array<
    keyof typeof citationLabels
  >) {
    const issue = sourceIssue(value.citation[key], citationLabels[key], [
      "dossier-citation",
      `dossier-citation:${key}`,
    ]);
    if (issue) return issue;
  }
  const citation = value.citation;
  if (
    citation.missing_fields.some((field) => !field.trim()) ||
    new Set(citation.missing_fields).size !== citation.missing_fields.length
  )
    return {
      label: "引注待补字段",
      keys: ["dossier-citation"],
      message: "引注待补字段请逐项填写，移除空白或重复项。",
    };
  const date = citation.publication_date;
  if (
    ((date.certainty === "stated" ||
      date.certainty === "inferred" ||
      date.normalized) &&
      (!date.text.trim() || !date.evidence_ids?.length)) ||
    date.evidence_ids?.some((id) => !available.has(id))
  )
    return {
      label: "出版时间",
      keys: ["dossier-citation", "dossier-citation:publication_date"],
      message: "出版时间需要保留原文表述及本卡依据；无可靠日期时可保留未明。",
    };
  if (citation.completeness === "complete") {
    for (const key of ["full", "short"] as const)
      if (citation[key].certainty === "unknown")
        return {
          label: citationLabels[key],
          keys: ["dossier-citation", `dossier-citation:${key}`],
          message: "引注仍有未明内容，请保留部分或未明状态，不补造完整引注。",
        };
    if (citation.missing_fields.length)
      return {
        label: "引注待补字段",
        keys: ["dossier-citation"],
        message: "引注仍列有待补字段，请核对完整度。",
      };
  }
  if (citation.completeness === "partial" && !citation.missing_fields.length)
    return {
      label: "引注待补字段",
      keys: ["dossier-citation"],
      message: "部分引注需要列明仍待补充的字段。",
    };
}

export default function CardDossierFields({
  value,
  evidence,
  onChange,
}: {
  value: Dossier;
  evidence: Draft["evidence"];
  onChange: (value: Dossier) => void;
}) {
  function source(key: keyof typeof sourceLabels, next: SourceValue) {
    onChange({ ...value, [key]: next });
  }
  function unit(
    index: number,
    next: Partial<Dossier["argument_units"][number]>,
  ) {
    onChange({
      ...value,
      argument_units: value.argument_units.map((item, at) =>
        at === index ? { ...item, ...next } : item,
      ),
    });
  }
  function citation(next: Partial<Dossier["citation"]>) {
    onChange({ ...value, citation: { ...value.citation, ...next } });
  }
  return (
    <>
      <DeferredFields summary="史料档案说明" editorKey="dossier">
        {() => (
          <>
            {(
              Object.keys(sourceLabels) as Array<keyof typeof sourceLabels>
            ).map((key) => (
              <DeferredFields
                key={key}
                summary={sourceLabels[key]}
                editorKey={`dossier:${key}`}
              >
                {() => (
                  <SourceEditor
                    label={sourceLabels[key]}
                    value={value[key]}
                    evidence={evidence}
                    multiline
                    unknownHint={unknownHint}
                    onChange={(next) => source(key, next)}
                  />
                )}
              </DeferredFields>
            ))}
          </>
        )}
      </DeferredFields>
      <DeferredFields summary="可用论证单元" editorKey="dossier-units">
        {() => (
          <>
            <p className="research-provenance">
              这里记录有原文依据的使用建议，并保留适用边界；不等同于原文作者已作出的结论。
            </p>
            {value.argument_units.map((item, index) => (
              <DeferredFields
                key={item.id}
                summary={`论证单元 ${index + 1} · ${item.title || "新增单元"}`}
                editorKey={`dossier-unit:${item.id}`}
                initiallyOpen={!item.claim}
              >
                {() => (
                  <fieldset className="research-edit-group">
                    <legend>论证单元 {index + 1}</legend>
                    <label>
                      标题
                      <Input
                        aria-label={`论证单元 ${index + 1} 标题`}
                        value={item.title}
                        onChange={(event) =>
                          unit(index, { title: event.target.value })
                        }
                      />
                    </label>
                    {(
                      [
                        "claim",
                        "applicability_limit",
                        "corroboration_needed",
                      ] as const
                    ).map((key) => (
                      <label key={key}>
                        {
                          {
                            claim: "论点",
                            applicability_limit: "适用边界",
                            corroboration_needed: "互证需求",
                          }[key]
                        }
                        <textarea
                          aria-label={`论证单元 ${index + 1} ${{ claim: "论点", applicability_limit: "适用边界", corroboration_needed: "互证需求" }[key]}`}
                          value={item[key]}
                          rows={3}
                          onChange={(event) =>
                            unit(index, { [key]: event.target.value })
                          }
                        />
                      </label>
                    ))}
                    <References
                      label={`论证单元 ${index + 1} 依据`}
                      value={item.evidence_ids}
                      evidence={evidence}
                      onChange={(evidence_ids) => unit(index, { evidence_ids })}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() =>
                        onChange({
                          ...value,
                          argument_units: value.argument_units.filter(
                            (_, at) => at !== index,
                          ),
                        })
                      }
                    >
                      移除论证单元 {index + 1}
                    </Button>
                  </fieldset>
                )}
              </DeferredFields>
            ))}
            <Button
              type="button"
              variant="outline"
              onClick={() =>
                onChange({
                  ...value,
                  argument_units: [
                    ...value.argument_units,
                    {
                      id: newId("argument"),
                      title: "",
                      claim: "",
                      evidence_ids: [],
                      applicability_limit: "",
                      corroboration_needed: "",
                    },
                  ],
                })
              }
            >
              添加论证单元
            </Button>
            <label>
              未列论证单元时的来源说明
              <textarea
                aria-label="未列论证单元说明"
                rows={3}
                value={value.argument_units_note}
                onChange={(event) =>
                  onChange({
                    ...value,
                    argument_units_note: event.target.value,
                  })
                }
              />
            </label>
          </>
        )}
      </DeferredFields>
      <DeferredFields summary="规范引注" editorKey="dossier-citation">
        {() => (
          <>
            <p className="research-provenance">
              只记录有依据的引注、出版时间和印刷页码；未详时保留未明，PDF页序不能代作印刷页码。
            </p>
            {(
              Object.keys(citationLabels) as Array<keyof typeof citationLabels>
            ).map((key) => (
              <DeferredFields
                key={key}
                summary={citationLabels[key]}
                editorKey={`dossier-citation:${key}`}
              >
                {() => (
                  <SourceEditor
                    label={citationLabels[key]}
                    value={value.citation[key]}
                    evidence={evidence}
                    multiline
                    unknownHint={unknownHint}
                    onChange={(next) => citation({ [key]: next })}
                  />
                )}
              </DeferredFields>
            ))}
            <DeferredFields
              summary="出版时间"
              editorKey="dossier-citation:publication_date"
            >
              {() => (
                <DateEditor
                  label="出版时间"
                  value={value.citation.publication_date}
                  evidence={evidence}
                  onChange={(publication_date) =>
                    citation({ publication_date })
                  }
                />
              )}
            </DeferredFields>
            <label>
              引注完整度
              <select
                value={value.citation.completeness}
                onChange={(event) =>
                  citation({
                    completeness: event.target
                      .value as Dossier["citation"]["completeness"],
                  })
                }
              >
                <option value="complete">完整</option>
                <option value="partial">部分，仍有待补字段</option>
                <option value="unknown">未明</option>
              </select>
            </label>
            <label>
              引注待补字段（每行一项）
              <textarea
                aria-label="引注待补字段"
                rows={3}
                value={value.citation.missing_fields.join("\n")}
                onChange={(event) =>
                  citation({ missing_fields: lines(event.target.value) })
                }
              />
            </label>
          </>
        )}
      </DeferredFields>
    </>
  );
}
