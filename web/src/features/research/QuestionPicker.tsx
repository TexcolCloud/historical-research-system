import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { research, researchKeys, type QuestionEvidenceInput } from "./api";

export default function QuestionPicker({
  material,
}: {
  material: QuestionEvidenceInput;
}) {
  const [open, setOpen] = useState(false),
    [selected, setSelected] = useState(""),
    [note, setNote] = useState("");
  const query = useQueryClient();
  const questions = useQuery({
    queryKey: researchKeys.questions,
    queryFn: ({ signal }) => research.questions(signal),
    enabled: open,
    staleTime: 30000,
  });
  const chosen = questions.data?.items.find((item) => item.id === selected);
  const save = useMutation({
    mutationFn: () => {
      if (!chosen) throw new Error("请先选择研究问题。");
      return research.questionEvidence(
        chosen.id,
        chosen.version,
        material,
        "saved",
        note,
      );
    },
    onSuccess: (saved) => {
      query.setQueryData(researchKeys.question(saved.id), saved);
      void query.invalidateQueries({ queryKey: researchKeys.questions });
    },
  });
  return (
    <div className="research-question-picker">
      <Button
        variant="ghost"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        保存到研究问题
      </Button>
      {open && (
        <div>
          {questions.isPending && <p role="status">正在读取研究问题…</p>}
          {questions.isError && <p role="alert">{questions.error.message}</p>}
          {questions.data?.items.length ? (
            <>
              <label>
                选择研究问题
                <select
                  aria-label="选材所属问题"
                  value={selected}
                  onChange={(event) => {
                    setSelected(event.target.value);
                    save.reset();
                  }}
                >
                  <option value="">请选择</option>
                  {questions.data.items
                    .filter((item) => item.status !== "archived")
                    .map((item) => (
                      <option value={item.id} key={item.id}>
                        {item.title}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                选材说明
                <textarea
                  aria-label="此次选材说明"
                  value={note}
                  onChange={(event) => {
                    setNote(event.target.value);
                    save.reset();
                  }}
                  rows={2}
                />
              </label>
              <Button
                disabled={!chosen || save.isPending || save.isSuccess}
                onClick={() => save.mutate()}
              >
                保存选材
              </Button>
            </>
          ) : (
            <Link to="/research?view=questions">先新建研究问题</Link>
          )}
          {save.isSuccess && (
            <p role="status">
              选材已保存。
              <Link
                to={`/research?view=questions&question=${encodeURIComponent(save.data.id)}`}
              >
                打开研究问题
              </Link>
            </p>
          )}
          {save.isError && (
            <p role="alert">
              {save.error.message}
              <Button variant="ghost" onClick={() => void questions.refetch()}>
                重新读取问题
              </Button>
            </p>
          )}
        </div>
      )}
    </div>
  );
}
