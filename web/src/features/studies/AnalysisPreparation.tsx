import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { studies } from "./api";
import type { StudyAnalysis, StudyTopic } from "./types";

export const analysisModeLabels = {
  summary: "材料摘要",
  compare: "记载对照",
  leads: "后续线索建议",
};

/** Preparation stays mounted while results are read, retaining an unfinished request. */
export default function AnalysisPreparation({
  topic,
  running,
  visible,
  onCreated,
}: {
  topic: StudyTopic;
  running: boolean;
  visible: boolean;
  onCreated: (analysis: StudyAnalysis) => void;
}) {
  const [mode, setMode] = useState<StudyAnalysis["mode"]>("summary");
  const [materials, setMaterials] = useState<string[]>([]);
  const [prompt, setPrompt] = useState("");
  const [opened, setOpened] = useState(visible);
  useEffect(() => {
    if (visible) setOpened(true);
  }, [visible]);
  const unavailable = materials.filter(
    (id) =>
      !topic.materials.some(
        (material) => material.id === id && material.source_current,
      ),
  );
  const missing = materials.filter(
    (id) => !topic.materials.some((material) => material.id === id),
  );
  const create = useMutation({
    mutationFn: () =>
      studies.createAnalysis(topic.id, {
        mode,
        material_ids: materials,
        prompt,
      }),
    onSuccess: onCreated,
  });
  if (!visible && !opened) return null;
  return (
    <div hidden={!visible}>
      <form
        className="study-editor"
        onSubmit={(event) => {
          event.preventDefault();
          if (materials.length && !unavailable.length && !topic.archived)
            create.mutate();
        }}
      >
        <h2>选择分析范围</h2>
        <label>
          分析方式
          <select
            aria-label="分析方式"
            value={mode}
            onChange={(event) =>
              setMode(event.target.value as StudyAnalysis["mode"])
            }
          >
            {Object.entries(analysisModeLabels).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="study-source-choices">
          <legend>使用的材料 · 已选 {materials.length} / 12 项</legend>
          {topic.materials.map((material) => (
            <label key={material.id} className="study-check">
              <input
                type="checkbox"
                checked={materials.includes(material.id)}
                disabled={
                  (!material.source_current &&
                    !materials.includes(material.id)) ||
                  (!materials.includes(material.id) && materials.length >= 12)
                }
                onChange={(event) =>
                  setMaterials(
                    event.target.checked
                      ? [...materials, material.id]
                      : materials.filter((id) => id !== material.id),
                  )
                }
              />
              {material.title}
              {!material.source_current && " · 来源待核"}
            </label>
          ))}
          {!topic.materials.length && <p>先在专题中添加已核验史料卡。</p>}
          {missing.map((id) => (
            <div key={id}>
              <p>此选材已移出当前专题，原选择仍保留。</p>
              <Button
                type="button"
                variant="outline"
                onClick={() =>
                  setMaterials(materials.filter((value) => value !== id))
                }
              >
                移除不可用选材
              </Button>
            </div>
          ))}
        </fieldset>
        {!!unavailable.length && (
          <p role="status">
            已选材料发生变化，请取消不可用项或更新专题选材后再提交。
          </p>
        )}
        <label>
          关注的问题或比较角度
          <textarea
            aria-label="分析关注点"
            value={prompt}
            maxLength={6000}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="例如：比较几份材料对同一措施的记载差异。"
          />
        </label>
        <p className="study-muted">
          建议与引用会单独核对。采纳后成为个人研究记录，不授予原文内容审批。
        </p>
        {create.isError && <p role="alert">{create.error.message}</p>}
        <Button
          type="submit"
          disabled={
            !materials.length ||
            !!unavailable.length ||
            create.isPending ||
            topic.archived
          }
        >
          {create.isPending ? "正在提交…" : "分析所选材料"}
        </Button>
        {running && (
          <p role="status">已有分析任务进行中，可继续阅读和整理其他记录。</p>
        )}
      </form>
    </div>
  );
}
