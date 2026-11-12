import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useCanvasResources } from "./canvas-resources";
import CanvasSession from "./canvas-session";
import "@xyflow/react/dist/style.css";
import "./canvas.css";

/** Page composition; resources, document transactions and editing interactions have their own owners. */
export default function StudyCanvas({
  topicId,
  canvasId,
}: {
  topicId: string;
  canvasId?: string;
}) {
  const resources = useCanvasResources(topicId, canvasId);
  const [newTitle, setNewTitle] = useState("研究画布");
  const importInput = useRef<HTMLInputElement>(null);
  if (resources.error)
    return (
      <section className="study-canvas-loading" role="alert">
        <h1>暂时无法打开研究画布</h1>
        <p>{resources.error}</p>
        <Button onClick={resources.reload}>重新读取</Button>
      </section>
    );
  if (!resources.data) return <p role="status">正在打开专题画布…</p>;
  const { topic, canvas, canvases, records, recordPage, workspace, activeId } =
    resources.data;
  return (
    <section className="study-canvas-workspace" aria-label="专题研究画布">
      <header className="study-canvas-context">
        <Link
          to={`/studies?topic=${encodeURIComponent(topicId)}`}
          className="study-canvas-back"
        >
          <ArrowLeft size={16} />
          {topic.title}
        </Link>
        <label>
          画布
          <select
            aria-label="选择研究画布"
            value={activeId || ""}
            onChange={(event) => resources.selectCanvas(event.target.value)}
          >
            <option value="" disabled>
              选择画布
            </option>
            {canvases.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
            {canvas && !canvases.some((item) => item.id === canvas.id) && (
              <option value={canvas.id}>{canvas.title}</option>
            )}
          </select>
        </label>
        <label className="study-canvas-archive-filter">
          <input
            type="checkbox"
            checked={resources.archived}
            onChange={(event) => resources.setArchived(event.target.checked)}
          />
          查看已归档
        </label>
        <details className="study-canvas-create">
          <summary>新建画布</summary>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void resources.createCanvas(newTitle);
            }}
          >
            <label>
              画布名称
              <input
                aria-label="新画布名称"
                value={newTitle}
                maxLength={200}
                onChange={(event) => setNewTitle(event.target.value)}
              />
            </label>
            <Button disabled={resources.creating || topic.archived}>
              创建画布
            </Button>
          </form>
        </details>
        <Button
          variant="outline"
          disabled={resources.creating || topic.archived}
          onClick={() => importInput.current?.click()}
        >
          恢复备份
        </Button>
        <input
          hidden
          ref={importInput}
          type="file"
          accept=".json,application/json"
          aria-label="恢复画布备份"
          onChange={(event) => void resources.importBackup(event.currentTarget)}
        />
      </header>
      {resources.createError && <p role="alert">{resources.createError}</p>}
      {resources.importNotice && (
        <p role="status" className="study-canvas-notice">
          {resources.importNotice}
        </p>
      )}
      {!canvas ? (
        <div className="study-canvas-empty">
          <h1>把材料放在同一张研究桌面上</h1>
          <p>从史料、论据和个人札记开始，自由摆放、分组并说明它们的关系。</p>
          <Button
            disabled={resources.creating || topic.archived}
            onClick={() => void resources.createCanvas("研究画布")}
          >
            <Plus size={16} />
            创建第一张画布
          </Button>
        </div>
      ) : (
        <CanvasSession
          key={canvas.id}
          canvas={canvas}
          topic={topic}
          records={records}
          recordPage={recordPage}
          workspace={workspace}
          createCanvas={resources.createCanvas}
          onArchived={resources.onArchived}
        />
      )}
    </section>
  );
}
