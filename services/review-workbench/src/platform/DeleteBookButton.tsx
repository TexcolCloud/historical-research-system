import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2, LoaderCircle } from "lucide-react";
import { Button } from "../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "../components/ui/dialog";
import { client, type Book } from "./client";
import { patchBookDeletion } from "./events";

export default function DeleteBookButton({ book }: { book: Book }) {
  const [open, setOpen] = useState(false);
  const query = useQueryClient();
  const deletion = useMutation({
    mutationFn: async () => {
      const { data, error } = await client.DELETE("/api/v2/books/{book_id}", {
        params: { path: { book_id: book.id } },
      });
      if (!data)
        throw new Error(
          error?.detail ? String(error.detail) : "删除请求未确认，请重试。",
        );
      return data;
    },
    onSuccess: (data) => {
      patchBookDeletion(
        query,
        book.id,
        data.state === "completed" ? "deleted" : "deleting",
      );
      setOpen(false);
    },
  });
  const busy = book.state === "deleting" || deletion.isPending;
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        disabled={busy}
        aria-label={`删除任务：${book.title}`}
        onClick={() => {
          deletion.reset();
          setOpen(true);
        }}
      >
        {busy ? <LoaderCircle className="animate-spin" /> : <Trash2 />}
        {busy
          ? "正在删除…"
          : book.state === "delete_failed"
            ? "重试删除"
            : "删除任务"}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除这本书的任务？</DialogTitle>
            <DialogDescription>
              将删除《{book.title}
              》及其上传文件、识别结果、章节和史料卡。正在执行的任务会自动终止，清理完成后从列表移除。此操作无法撤销。
            </DialogDescription>
          </DialogHeader>
          {deletion.isError && <p role="alert">{deletion.error.message}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              variant="destructive"
              disabled={deletion.isPending}
              onClick={() => deletion.mutate()}
            >
              {deletion.isPending ? "正在提交…" : "确认删除"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
