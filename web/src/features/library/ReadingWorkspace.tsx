import { useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button.tsx";
import type { Book } from "@/client/api.ts";
import ReaderPanel from "./ReaderPanel.tsx";
import NavigationPanel from "./NavigationPanel.tsx";
import ConversionReader from "@/features/review/ConversionReader.tsx";

export default function ReadingWorkspace({ book }: { book: Book }) {
  const [params, setParams] = useSearchParams();
  const legacy = params.get("tab");
  const section =
    legacy === "navigation"
      ? "directory"
      : legacy === "search"
        ? "search"
        : (params.get("section") ?? "text");
  const published = book.state === "ready" || book.revision_pending;
  function choose(value: string) {
    const next = new URLSearchParams(params);
    next.set("tab", "reader");
    next.set("section", value);
    setParams(next);
  }
  return (
    <section className="reading-workspace">
      {(section === "directory" || !published) && (
        <div className="reading-workspace-tools">
          <Button variant="outline" onClick={() => choose("text")}>
            {published ? "返回正文" : "阅读底稿"}
          </Button>
          {section !== "directory" && (
            <Button variant="ghost" onClick={() => choose("directory")}>
              原书目录
            </Button>
          )}
        </div>
      )}
      {book.revision_pending && (
        <p className="workspace-status-note">
          新版本核对中，正式阅读与检索使用此前发布的版本。
        </p>
      )}
      {section === "directory" ? (
        book.run_id ? (
          <NavigationPanel
            runId={book.run_id}
            bookId={book.id}
            bookState={book.state}
            published={published}
          />
        ) : (
          <p>接收文件后会开始发现目录。</p>
        )
      ) : published ? (
        <ReaderPanel bookId={book.id} />
      ) : section === "search" ? (
        <div className="workspace-empty">
          <h2>本书尚未建立检索索引</h2>
          <p>完成内容核对和入库后开放正式检索。你可以先阅读已有底稿。</p>
          <Button onClick={() => choose("text")}>先阅读底稿</Button>
          {(book.pending_count ?? 0) > 0 && (
            <a href={`#/books/${book.id}?tab=review`}>处理待核对内容</a>
          )}
        </div>
      ) : book.run_id ? (
        <>
          <div className="workspace-status-note">
            <strong>识别底稿 · 正式阅读版尚未生成</strong>
            <p>
              此处可阅读已保存的文字与原图，各页核对状态单独标示；所需核对与结构整理完成后生成正式阅读版。
            </p>
            {(book.pending_count ?? 0) > 0 && (
              <a href={`#/books/${book.id}?tab=review`}>
                去核对 {book.pending_count} 页正文
              </a>
            )}
          </div>
          <ConversionReader runId={book.run_id} />
        </>
      ) : (
        <div className="workspace-empty">
          <h2>原件正在接收</h2>
          <p>接收后自动生成可阅读底稿，关闭页面不影响后台处理。</p>
        </div>
      )}
    </section>
  );
}
