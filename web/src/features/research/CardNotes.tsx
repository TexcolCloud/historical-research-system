import { useEffect, useState } from "react";
import {
  skipToken,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { research, type CardNotes as Notes } from "./api";

export default function CardNotes({ cardId }: { cardId: string }) {
  const query = useQueryClient();
  const draftKey = ["research", "notes-draft", cardId];
  const draft = useQuery<{ base: Notes; text: string } | null>({
    queryKey: draftKey,
    queryFn: skipToken,
    gcTime: Infinity,
  });
  const [open, setOpen] = useState(false);
  const [base, setBase] = useState<Notes | undefined>(draft.data?.base);
  const [text, setText] = useState(draft.data?.text ?? "");
  const key = ["research", "notes", cardId];
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => research.notes(cardId, signal),
    enabled: open,
    staleTime: 30000,
    retry: false,
  });
  const dirty = Boolean(base && text !== base.text);
  useEffect(() => {
    if (!dirty) return;
    const prevent = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty]);
  const save = useMutation({
    onMutate: async () => {
      await query.cancelQueries({ queryKey: key });
    },
    mutationFn: () => research.updateNotes(cardId, base!.version, text),
    onSuccess: (saved) => {
      setBase(saved);
      setText(saved.text);
      query.setQueryData(draftKey, null);
      query.setQueryData(key, saved);
      void query.invalidateQueries({ queryKey: ["research", "card"] });
    },
  });
  useEffect(() => {
    if (detail.data && !dirty && !save.isError) {
      setBase(detail.data);
      setText(detail.data.text);
    }
  }, [detail.data, dirty, save.isError]);
  const newer = detail.data && base && detail.data.version !== base.version;
  return (
    <details
      className="research-card-notes"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>人工备注</summary>
      {open && (
        <>
          <p className="research-provenance">
            个人阅读记录，可选填；保存不会改变卡片内容或核验状态。
          </p>
          {detail.isPending && <p role="status">正在读取人工备注…</p>}
          {detail.isError && (
            <p role="alert">
              {detail.error.message}
              <Button variant="ghost" onClick={() => void detail.refetch()}>
                重新读取备注
              </Button>
            </p>
          )}
          {base && (
            <>
              <label>
                备注内容
                <textarea
                  aria-label="人工备注内容"
                  rows={5}
                  maxLength={60000}
                  value={text}
                  onChange={(event) => {
                    const value = event.target.value;
                    setText(value);
                    query.setQueryData(
                      draftKey,
                      value === base.text ? null : { base, text: value },
                    );
                  }}
                  disabled={save.isPending}
                />
              </label>
              <Button
                disabled={!dirty || save.isPending || newer}
                onClick={() => save.mutate()}
              >
                保存备注
              </Button>
              <p className="research-provenance">备注版本 {base.version}</p>
              {dirty && (
                <p role="status">
                  备注尚未保存。切换页面后返回本卡可继续编辑；刷新或关闭前请先保存。
                </p>
              )}
            </>
          )}
          {save.isPending && <p role="status">正在保存人工备注…</p>}
          {save.isSuccess && !dirty && (
            <p role="status">人工备注已保存，卡片核验状态保持。</p>
          )}
          {save.isError && (
            <p role="alert">
              {save.error.message} 当前输入仍保留。
              <Button variant="outline" onClick={() => void detail.refetch()}>
                读取最新备注
              </Button>
            </p>
          )}
          {newer && detail.data && (
            <section
              className="workspace-status-note"
              aria-label="备注版本对照"
            >
              <h4>最新备注</h4>
              <p className="research-preserve">
                {detail.data.text || "最新备注为空。"}
              </p>
              <Button
                variant="outline"
                onClick={() => {
                  setBase(detail.data);
                  query.setQueryData(draftKey, { base: detail.data, text });
                  save.reset();
                }}
              >
                已核对最新备注，保留输入继续保存
              </Button>
            </section>
          )}
        </>
      )}
    </details>
  );
}
