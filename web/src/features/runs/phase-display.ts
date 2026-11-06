/** Display vocabulary only; run and book states retain their own semantics. */
export const stageLabels: Record<string, string> = {
  ocr: "文字提取",
  review: "原图核对",
  verify: "修正复核",
  navigation: "目录关联",
  organization: "文章整理",
};
export const phases = [
  { title: "接收原件", description: "文件上传与完整性校验" },
  { title: "文字提取", description: "提取 PDF 文字或识别扫描页，逐页保存结果" },
  { title: "原图核对", description: "对照原图逐页检查提取的文字" },
  { title: "人工核对", description: "处理机器发现的错误与不确定项" },
  { title: "整理与入库", description: "组装整书章节、建立出处与检索索引" },
];
