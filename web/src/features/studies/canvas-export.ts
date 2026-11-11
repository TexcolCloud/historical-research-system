import { isCanvasNodeHidden, worldPosition } from "./canvas-model";
import type { StudyCanvasDocument } from "./types";

export type CanvasLabel = {
  title: string;
  kind: string;
  text: string;
  meta: string;
};

export function downloadCanvasFile(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name.replace(/[<>:"/\\|?*]/g, "_");
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const escape = (value: string) =>
  value.replace(
    /[&<>"']/g,
    (part) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&apos;",
      })[part]!,
  );
const lines = (text: string, columns: number) =>
  (text.match(new RegExp(`[^\\n]{1,${columns}}|\\n`, "gu")) || []).map((line) =>
    line.trim(),
  );

/** Portable drawing: no foreign HTML, remote images, credentials or executable content. */
function canvasSVG(
  value: StudyCanvasDocument,
  labels: Map<string, CanvasLabel>,
  palette = {
    paper: "#f7f6f1",
    card: "#ffffff",
    ink: "#263d34",
    border: "#c8cec4",
  },
) {
  const nodes = value.nodes.filter((node) => !isCanvasNodeHidden(value, node));
  const boxes = new Map(
    nodes.map((node) => [
      node.id,
      {
        ...worldPosition(value, node),
        width: node.width,
        height: node.collapsed ? 48 : node.height,
      },
    ]),
  );
  const x = Math.min(0, ...[...boxes.values()].map((box) => box.x)) - 32;
  const y = Math.min(0, ...[...boxes.values()].map((box) => box.y)) - 32;
  const width =
    Math.max(400, ...[...boxes.values()].map((box) => box.x + box.width)) -
    x +
    32;
  const height =
    Math.max(300, ...[...boxes.values()].map((box) => box.y + box.height)) -
    y +
    64;
  const elements: string[] = [];
  for (const node of nodes.filter((node) => node.record_kind === "group")) {
    const box = boxes.get(node.id)!;
    elements.push(
      `<rect x="${box.x}" y="${box.y}" width="${box.width}" height="${box.height}" rx="8" fill="${escape(palette.paper)}" stroke="${escape(palette.border)}"/><text x="${box.x + 16}" y="${box.y + 29}" font-size="16">分组 · ${escape(node.title || "未命名分组")}</text>`,
    );
  }
  for (const edge of value.edges) {
    const from = boxes.get(edge.source),
      to = boxes.get(edge.target);
    if (!from || !to) continue;
    const x1 = from.x + from.width,
      y1 = from.y + from.height / 2;
    const x2 = to.x,
      y2 = to.y + to.height / 2;
    elements.push(
      `<path d="M${x1} ${y1} C${x1 + 60} ${y1},${x2 - 60} ${y2},${x2} ${y2}" fill="none" stroke="${escape(palette.ink)}" stroke-width="1.5" marker-end="url(#arrow)"/><text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 9}" font-size="12" text-anchor="middle">${escape(edge.label)}</text>`,
    );
  }
  for (const node of nodes.filter((node) => node.record_kind !== "group")) {
    const box = boxes.get(node.id)!,
      label = labels.get(node.id) || {
        title: node.title || "记录",
        kind: "个人记录",
        text: "",
        meta: "",
      };
    const columns = Math.max(8, Math.floor((box.width - 32) / 15));
    const title = lines(label.title, columns).slice(0, 2);
    const text = lines(label.text.replace(/[#*_`]/g, ""), columns).slice(
      0,
      Math.max(0, Math.floor((box.height - 116) / 23)),
    );
    elements.push(
      `<rect x="${box.x}" y="${box.y}" width="${box.width}" height="${box.height}" rx="7" fill="${escape(palette.card)}" stroke="${escape(palette.border)}"/><text x="${box.x + 16}" y="${box.y + 24}" font-size="11">${escape(label.kind)}</text>`,
    );
    title.forEach((line, index) =>
      elements.push(
        `<text x="${box.x + 16}" y="${box.y + 51 + index * 22}" font-size="16">${escape(line)}</text>`,
      ),
    );
    text.forEach((line, index) =>
      elements.push(
        `<text x="${box.x + 16}" y="${box.y + 100 + index * 23}" font-size="13">${escape(line)}</text>`,
      ),
    );
    elements.push(
      `<text x="${box.x + 16}" y="${box.y + box.height - 16}" font-size="10">${escape(label.meta.slice(0, columns * 2))}</text>`,
    );
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="${x} ${y} ${width} ${height}"><metadata>${escape(JSON.stringify({ kind: "personal-study-canvas", source_references: value.nodes.map((node) => ({ node_id: node.id, kind: node.record_kind, record_id: node.record_id })) }))}</metadata><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="${escape(palette.ink)}"/></marker></defs><rect x="${x}" y="${y}" width="${width}" height="${height}" fill="${escape(palette.paper)}"/><g fill="${escape(palette.ink)}" font-family="sans-serif">${elements.join("")}<text x="${x + 16}" y="${y + height - 18}" font-size="11">个人研究整理 · 连线表达研究关系；完整引文与出处见 Markdown 导出。</text></g></svg>`;
  return { svg, width, height };
}

export async function exportCanvasImage(
  value: StudyCanvasDocument,
  labels: Map<string, CanvasLabel>,
  title: string,
  format: "svg" | "png",
) {
  await document.fonts?.ready;
  const style = getComputedStyle(document.documentElement);
  const drawing = canvasSVG(value, labels, {
    paper: style.getPropertyValue("--background").trim() || "#f7f6f1",
    card: style.getPropertyValue("--card").trim() || "#ffffff",
    ink: style.getPropertyValue("--foreground").trim() || "#263d34",
    border: style.getPropertyValue("--border").trim() || "#c8cec4",
  });
  const blob = new Blob([drawing.svg], { type: "image/svg+xml;charset=utf-8" });
  if (format === "svg") {
    downloadCanvasFile(blob, `${title}.svg`);
    return;
  }
  if (
    drawing.width > 12000 ||
    drawing.height > 12000 ||
    drawing.width * drawing.height > 40_000_000
  )
    throw new Error("画布范围过大，请导出 SVG 或先缩小内容范围，再生成 PNG。");
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () =>
        reject(new Error("无法生成图片，请使用 SVG 或结构化导出。"));
      image.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(drawing.width);
    canvas.height = Math.ceil(drawing.height);
    const context = canvas.getContext("2d");
    if (!context) throw new Error("浏览器无法生成 PNG，请使用 SVG 导出。");
    context.drawImage(image, 0, 0);
    const png = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/png"),
    );
    if (!png) throw new Error("PNG 生成失败，请保留布局并使用 SVG 导出。");
    downloadCanvasFile(png, `${title}.png`);
  } finally {
    URL.revokeObjectURL(url);
  }
}
