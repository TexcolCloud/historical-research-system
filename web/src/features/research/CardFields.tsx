import { Input } from "@/components/ui/input";
import type { components } from "@/client/schema";
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
type S = components["schemas"];
export type Draft = S["EditableCardResponse"]["card"];

export const CardEditorReveal = createContext<readonly string[]>([]);

/** The caller retains the draft; closed sections never construct their field trees. */
export function DeferredFields({
  summary,
  children,
  initiallyOpen = false,
  className,
  editorKey,
}: {
  summary: ReactNode;
  children: () => ReactNode;
  initiallyOpen?: boolean;
  className?: string;
  editorKey?: string;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const element = useRef<HTMLDetailsElement>(null);
  const revealed = useContext(CardEditorReveal);
  useEffect(() => {
    if (editorKey && revealed.includes(editorKey)) {
      setOpen(true);
      if (editorKey.includes(":"))
        element.current?.scrollIntoView?.({ block: "nearest" });
    }
  }, [editorKey, revealed]);
  return (
    <details
      ref={element}
      className={className}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>{summary}</summary>
      {open ? children() : null}
    </details>
  );
}
type DateValue = S["ResearchDate"];
type SourceValue = S["SourceValue"];
export const certainty = {
  stated: "原文明确记载",
  inferred: "推断",
  unknown: "未明",
  disputed: "有争议",
};
export const newId = (kind: string) =>
  `${kind}-${crypto.randomUUID().slice(0, 8)}`;
export const emptyValue = (): SourceValue => ({
  value: "",
  certainty: "unknown",
  evidence_ids: [],
  note: "本次尚未补充该项的可靠依据。",
});
export const emptyDate = (): DateValue => ({
  text: "",
  normalized: null,
  certainty: "unknown",
  evidence_ids: [],
});
export function References({
  label,
  value,
  evidence,
  onChange,
}: {
  label: string;
  value: string[];
  evidence: Draft["evidence"];
  onChange: (value: string[]) => void;
}) {
  return (
    <fieldset className="research-reference-selector">
      <legend>{label}</legend>
      {evidence.map((item, index) => (
        <label key={item.id}>
          <input
            type="checkbox"
            checked={value.includes(item.id)}
            onChange={(event) =>
              onChange(
                event.target.checked
                  ? [...value, item.id]
                  : value.filter((id) => id !== item.id),
              )
            }
          />
          <span>
            证据 {index + 1}：{item.quote.slice(0, 42)}
            {item.quote.length > 42 ? "…" : ""}
          </span>
        </label>
      ))}
    </fieldset>
  );
}
export function DateEditor({
  label,
  value,
  evidence,
  onChange,
}: {
  label: string;
  value: DateValue;
  evidence: Draft["evidence"];
  onChange: (value: DateValue) => void;
}) {
  return (
    <fieldset className="research-edit-group">
      <legend>{label}</legend>
      <label>
        {label}原文
        <Input
          aria-label={`${label}原文`}
          value={value.text}
          onChange={(event) => onChange({ ...value, text: event.target.value })}
        />
      </label>
      <label>
        日期状态
        <select
          value={value.certainty}
          onChange={(event) =>
            onChange({
              ...value,
              certainty: event.target.value as DateValue["certainty"],
            })
          }
        >
          {Object.entries(certainty).map(([key, text]) => (
            <option key={key} value={key}>
              {text}
            </option>
          ))}
        </select>
      </label>
      <label>
        规范日期（可以留空）
        <Input
          value={value.normalized ?? ""}
          onChange={(event) =>
            onChange({ ...value, normalized: event.target.value || null })
          }
        />
      </label>
      <References
        label={`${label}依据`}
        value={value.evidence_ids ?? []}
        evidence={evidence}
        onChange={(evidence_ids) => onChange({ ...value, evidence_ids })}
      />
    </fieldset>
  );
}
export function SourceEditor({
  label,
  value,
  evidence,
  onChange,
}: {
  label: string;
  value: SourceValue;
  evidence: Draft["evidence"];
  onChange: (value: SourceValue) => void;
}) {
  return (
    <fieldset className="research-edit-group">
      <legend>{label}</legend>
      <label>
        记载状态
        <select
          value={value.certainty}
          onChange={(event) => {
            const next = event.target.value as SourceValue["certainty"];
            onChange({
              ...value,
              certainty: next,
              value: next === "unknown" ? "" : value.value,
            });
          }}
        >
          {Object.entries(certainty).map(([key, text]) => (
            <option value={key} key={key}>
              {text}
            </option>
          ))}
        </select>
      </label>
      <label>
        {label}记载
        <Input
          aria-label={`${label}记载`}
          disabled={value.certainty === "unknown"}
          value={value.value}
          onChange={(event) =>
            onChange({ ...value, value: event.target.value })
          }
        />
      </label>
      <p className="research-provenance">
        未明时请注明原因，例如旧卡未提取或缺少落款；不要填入猜测。
      </p>
      <label>
        说明
        <Input
          value={value.note}
          onChange={(event) => onChange({ ...value, note: event.target.value })}
        />
      </label>
      <References
        label={`${label}依据`}
        value={value.evidence_ids ?? []}
        evidence={evidence}
        onChange={(evidence_ids) => onChange({ ...value, evidence_ids })}
      />
    </fieldset>
  );
}
