import { useEffect, useRef, useState } from "react";
import { EditorContent, useEditor, useEditorState } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Markdown } from "@tiptap/markdown";
import {
  Table,
  TableRow,
  TableCell,
  TableHeader,
} from "@tiptap/extension-table";
import { RenderedMarkdown } from "./RenderedMarkdown.tsx";
import DOMPurify from "dompurify";
import { splitMarkdown } from "./markdown.ts";

/** Reject structures the visual editor cannot preserve. Source mode remains available. */
export function tableEditProblem(raw: string): string | null {
  const doc = new DOMParser().parseFromString(raw, "text/html");
  if (
    doc.body.children.length !== 1 ||
    doc.body.firstElementChild?.tagName !== "TABLE"
  )
    return "混合 HTML 内容，请使用源码模式。";
  if (raw.includes("<!--")) return "表格内含注释，请使用源码模式以保留。";
  const allowed = new Set([
    "TABLE",
    "THEAD",
    "TBODY",
    "TFOOT",
    "TR",
    "TH",
    "TD",
    "P",
    "BR",
    "STRONG",
    "B",
    "EM",
    "I",
    "S",
    "STRIKE",
    "CODE",
  ]);
  for (const node of doc.body.querySelectorAll("*")) {
    if (!allowed.has(node.tagName))
      return `包含暂不支持的 ${node.tagName} 元素，请使用源码模式。`;
    for (const attr of node.attributes)
      if (
        !["rowspan", "colspan"].includes(attr.name) ||
        !["TD", "TH"].includes(node.tagName)
      )
        return `包含 ${attr.name} 属性，请使用源码模式以保留。`;
  }
  const rows = Array.from(doc.querySelectorAll("tr")),
    grid: boolean[][] = rows.map(() => []);
  if (!rows.length) return "空表格请使用源码模式。";
  for (let r = 0; r < rows.length; r++) {
    let col = 0;
    for (const cell of Array.from(rows[r].cells)) {
      while (grid[r][col]) col++;
      const rs = cell.rowSpan,
        cs = cell.colSpan;
      if (rs < 1 || r + rs > rows.length || cs > 100 || col + cs > 100)
        return "跨行跨列范围异常，请先在源码中修复。";
      for (let y = r; y < r + rs; y++)
        for (let x = col; x < col + cs; x++) {
          if (grid[y][x]) return "单元格范围重叠，请先在源码中修复。";
          grid[y][x] = true;
        }
      col += cs;
    }
  }
  const width = Math.max(...grid.map((row) => row.length));
  if (
    grid.some(
      (row) =>
        row.length !== width ||
        Array.from({ length: width }, (_, x) => row[x]).some((v) => !v),
    )
  )
    return "表格存在不齐的单元格；为避免自动补格，请先在源码中修复。";
  return null;
}

const HtmlTable = Table.extend({
  renderHTML() {
    return ["table", ["tbody", 0]];
  },
});

function BlockEditor({
  raw,
  table,
  onChange,
}: {
  raw: string;
  table: boolean;
  onChange: (raw: string) => void;
}) {
  const change = useRef(onChange);
  change.current = onChange;
  const ending = useRef(raw.match(/(?:\r?\n)+$/)?.[0] ?? "\n\n");
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        link: { openOnClick: false },
        trailingNode: false,
      }),
      Markdown,
      HtmlTable.configure({ resizable: false }),
      TableRow,
      TableCell,
      TableHeader,
    ],
    content: table ? DOMPurify.sanitize(raw) : raw,
    contentType: table ? "html" : "markdown",
    immediatelyRender: false,
    editorProps: {
      attributes: {
        role: "textbox",
        "aria-multiline": "true",
        "aria-label": table ? "可视化表格编辑器" : "可视化正文编辑器",
        class: "prose block-prosemirror",
      },
    },
    onUpdate: ({ editor }) =>
      change.current(
        (table ? editor.getHTML() : editor.getMarkdown()).trimEnd() +
          ending.current,
      ),
  });
  const paragraphStyle = useEditorState({
    editor,
    selector: ({ editor: current }) => {
      if (!current) return "0";
      return String(
        [1, 2, 3, 4, 5, 6].find((level) =>
          current.isActive("heading", { level }),
        ) || 0,
      );
    },
  });
  useEffect(() => {
    editor?.commands.focus("start");
  }, [editor]);
  if (!editor) return null;
  const commands = table
    ? ([
        ["左侧加列", () => editor.chain().focus().addColumnBefore().run()],
        ["右侧加列", () => editor.chain().focus().addColumnAfter().run()],
        ["删除列", () => editor.chain().focus().deleteColumn().run()],
        ["上方加行", () => editor.chain().focus().addRowBefore().run()],
        ["下方加行", () => editor.chain().focus().addRowAfter().run()],
        ["删除行", () => editor.chain().focus().deleteRow().run()],
        ["合并单元格", () => editor.chain().focus().mergeCells().run()],
        ["拆分单元格", () => editor.chain().focus().splitCell().run()],
      ] as const)
    : ([
        ["加粗", () => editor.chain().focus().toggleBold().run()],
        ["斜体", () => editor.chain().focus().toggleItalic().run()],
        ["无序列表", () => editor.chain().focus().toggleBulletList().run()],
        ["有序列表", () => editor.chain().focus().toggleOrderedList().run()],
        ["引用", () => editor.chain().focus().toggleBlockquote().run()],
      ] as const);
  return (
    <div className="active-block">
      <div className="block-tools" role="toolbar" aria-label="内容编辑工具">
        {!table && (
          <label>
            段落样式{" "}
            <select
              aria-label="段落样式"
              value={paragraphStyle ?? "0"}
              onChange={(event) => {
                const level = Number(event.target.value);
                if (level === 0) editor.chain().focus().setParagraph().run();
                else if (level >= 1 && level <= 6)
                  editor
                    .chain()
                    .focus()
                    .setHeading({ level: level as 1 | 2 | 3 | 4 | 5 | 6 })
                    .run();
              }}
            >
              <option value="0">正文段落</option>
              {[1, 2, 3, 4, 5, 6].map((level) => (
                <option key={level} value={level}>
                  {level} 级标题
                </option>
              ))}
            </select>
          </label>
        )}
        {commands.map(([label, run]) => (
          <button
            key={label}
            type="button"
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => run()}
          >
            {label}
          </button>
        ))}
        <button
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => editor.chain().focus().undo().run()}
        >
          撤销
        </button>
        <button
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => editor.chain().focus().redo().run()}
        >
          重做
        </button>
      </div>
      {table && (
        <p className="field-hint">
          先点选单元格再增删行列；拖选多个单元格后可合并。修改后仍保存为 HTML
          表格。
        </p>
      )}
      <EditorContent editor={editor} />
    </div>
  );
}

export function MarkdownEditor({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const [blocks, setBlocks] = useState(() => splitMarkdown(value));
  const [active, setActive] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  function update(index: number, raw: string) {
    const next = blocks.map((block, at) =>
      at === index ? { ...block, raw } : block,
    );
    setBlocks(next);
    onChange(next.map((block) => block.raw).join(""));
  }
  return (
    <div className="markdown-document">
      <p className="editing-hint">
        点击正文或表格开始编辑 · 未编辑内容保留原始 Markdown
      </p>
      {message && (
        <div className="notice" role="status">
          {message}
        </div>
      )}
      {blocks.map((block, index) => {
        if (!block.raw.trim()) return null;
        const table =
          block.type === "html" && /^\s*<table[\s>]/i.test(block.raw);
        const unsupported =
          block.type === "source" ||
          (block.type === "html" && !table) ||
          (!table && /<\/?[a-z!]|!\[|\[[ xX]\]/i.test(block.raw));
        if (active === index)
          return (
            <div key={index}>
              <BlockEditor
                raw={block.raw}
                table={table}
                onChange={(raw) => update(index, raw)}
              />
            </div>
          );
        const activate = () => {
          const problem = unsupported
            ? "此内容包含特殊语法；请切换“Markdown 源码”编辑，原始内容已保留。"
            : table
              ? tableEditProblem(block.raw)
              : null;
          if (problem) {
            setMessage(problem);
            return;
          }
          setMessage("");
          setActive(index);
        };
        return (
          <section
            className="editable-block"
            key={index}
            tabIndex={0}
            aria-label={table ? "编辑 HTML 表格" : `编辑内容块 ${index + 1}`}
            onClick={activate}
            onKeyDown={(event) => {
              if (event.key === "Enter" && event.target === event.currentTarget)
                activate();
            }}
          >
            {block.raw.trim().startsWith("<!--") ? (
              <span className="source-note">
                HTML 注释已保留，可在源码中编辑
              </span>
            ) : (
              <RenderedMarkdown markdown={block.raw} />
            )}
          </section>
        );
      })}
    </div>
  );
}
