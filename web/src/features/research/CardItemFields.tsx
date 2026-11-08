import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { References, certainty, newId, type Draft } from "./CardFields";

type Item = NonNullable<Draft["items"]>[number];
const kinds = {
  section: "内容层次",
  evidence: "论据",
  argument: "论证",
  issue: "问题",
  paper_use: "论文用途",
};
export default function CardItemFields({
  value,
  evidence,
  onChange,
  removeBlocked,
}: {
  value: Item[];
  evidence: Draft["evidence"];
  onChange: (value: Item[]) => void;
  removeBlocked: string[];
}) {
  function patch(index: number, next: Partial<Item>) {
    onChange(
      value.map((item, at) => (at === index ? { ...item, ...next } : item)),
    );
  }
  return (
    <section>
      <h4>内容条目</h4>
      {value.map((item, index) => (
        <fieldset className="research-edit-group" key={item.id}>
          <legend>条目 {index + 1}</legend>
          <label>
            类别
            <select
              value={item.kind}
              onChange={(event) =>
                patch(index, { kind: event.target.value as Item["kind"] })
              }
            >
              {Object.entries(kinds).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            小标题
            <Input
              value={item.title}
              onChange={(event) => patch(index, { title: event.target.value })}
            />
          </label>
          <label>
            正文
            <textarea
              aria-label={`条目 ${index + 1} 正文`}
              value={item.text}
              rows={4}
              required
              onChange={(event) => patch(index, { text: event.target.value })}
            />
          </label>
          <label>
            归属
            <Input
              value={item.attribution}
              onChange={(event) =>
                patch(index, { attribution: event.target.value })
              }
            />
          </label>
          <label>
            判断状态
            <select
              value={item.epistemic_state}
              onChange={(event) =>
                patch(index, {
                  epistemic_state: event.target
                    .value as Item["epistemic_state"],
                })
              }
            >
              {Object.entries(certainty).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <References
            label="条目依据"
            value={item.evidence_ids ?? []}
            evidence={evidence}
            onChange={(evidence_ids) => patch(index, { evidence_ids })}
          />
          <label>
            使用边界（每行一项）
            <textarea
              value={(item.limitations ?? []).join("\n")}
              onChange={(event) =>
                patch(index, {
                  limitations: event.target.value
                    .split("\n")
                    .map((item) => item.trim())
                    .filter(Boolean),
                })
              }
            />
          </label>
          <Button
            type="button"
            variant="ghost"
            disabled={removeBlocked.includes(item.id)}
            onClick={() => onChange(value.filter((_, at) => at !== index))}
          >
            移除条目 {index + 1}
          </Button>
          {removeBlocked.includes(item.id) && (
            <p className="research-provenance">
              请先调整指向此条目的关系，再移除条目。
            </p>
          )}
        </fieldset>
      ))}
      <Button
        type="button"
        variant="outline"
        onClick={() =>
          onChange([
            ...value,
            {
              id: newId("item"),
              kind: "evidence",
              title: "",
              text: "",
              evidence_ids: [],
              epistemic_state: "stated",
              attribution: "",
              limitations: [],
            },
          ])
        }
      >
        添加内容条目
      </Button>
    </section>
  );
}
