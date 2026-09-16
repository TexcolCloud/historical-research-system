import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import Dashboard from "@uppy/react/dashboard";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog.tsx";
import { createBookUploader } from "@/features/uploads/uploads.ts";
import { booksKey } from "@/hooks/events.ts";
import type { Book } from "@/client/api.ts";
import "@uppy/core/css/style.min.css";
import "@uppy/dashboard/css/style.min.css";

export default function UploadPanel({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [{ uppy, cancelBooks }] = useState(createBookUploader),
    query = useQueryClient();
  useEffect(() => {
    const refresh = () => {
      void query.invalidateQueries({ queryKey: booksKey });
    };
    uppy.on("upload-success", refresh);
    const unsubscribe = query.getQueryCache().subscribe(() => {
      const closing =
        query
          .getQueryData<Book[]>(booksKey)
          ?.filter((book) =>
            ["deleting", "delete_failed", "deleted"].includes(book.state),
          )
          .map((book) => book.id) ?? [];
      cancelBooks(closing);
    });
    const prevent = (event: BeforeUnloadEvent) => {
      if (
        uppy
          .getFiles()
          .some((f) => f.progress.uploadStarted && !f.progress.uploadComplete)
      ) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", prevent);
    return () => {
      uppy.destroy();
      unsubscribe();
      window.removeEventListener("beforeunload", prevent);
    };
  }, [uppy, query, cancelBooks]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="platform-upload-dialog">
        <DialogHeader>
          <DialogTitle>导入书籍</DialogTitle>
          <DialogDescription>
            选择 PDF，上传后自动识别与核验。关闭此窗口不影响当前上传。
          </DialogDescription>
        </DialogHeader>
        <Dashboard
          uppy={uppy}
          height={360}
          proudlyDisplayPoweredByUppy={false}
          note="PDF · 每个文件最大 256 MB · 支持暂停与继续"
        />
      </DialogContent>
    </Dialog>
  );
}
