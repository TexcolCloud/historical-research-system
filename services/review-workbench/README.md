# Review Workbench

以书籍任务为中心的 React 工作台：书架、待办、史料卡，以及每本书的进展、阅读全文、内容核对、检索与任务执行图。

[首次启动](../../README.md#快速开始) · [业务行为](../research-platform/README.md#处理流程与数据职责) · [API 契约](../research-platform/openapi.json)

## 本地开发

需要 Node.js 24+、平台 Python 环境及可访问的后端／上传服务。下面命令在本模块目录执行：

```powershell
npm ci
npm --prefix tooling/openapi ci
npm run contracts
npm run dev
```

开发服务器默认 18156，不能与生产 Web 同时占用。需要并行开发时使用 `npm run dev -- --port 18157`。根配置的 `WORKBENCH_API_ORIGIN` 和 `WORKBENCH_UPLOAD_ORIGIN` 可覆盖默认代理目标 18170／18171，见 [vite.config.ts](vite.config.ts)。

生产 [Dockerfile](../../deploy/platform/Dockerfile.web) 自动构建前端，由 [Nginx](../../deploy/platform/nginx.conf) 同源代理 `/api/v2` 与 `/uploads/`。`/` 和 `/platform.html` 使用同一应用；无需额外启动 Vite。

## 页面与组件

| 功能                | 实现                                                      |
| ------------------- | --------------------------------------------------------- |
| 路由与数据          | React Router、TanStack Query、生成的 openapi-fetch 客户端 |
| 可恢复上传          | Uppy、Golden Retriever、tusd；最终文件保存在 S3           |
| Markdown 查看与编辑 | 渲染器及 Tiptap；编辑器按需加载                           |
| 执行关系图          | React Flow + Dagre，节点和连线由实际运行记录生成          |
| 界面基础            | Radix UI、Tailwind、Lucide；复用现有组件                  |

入口为 [PlatformApp.tsx](src/platform/PlatformApp.tsx)，书籍页为 [BookPage.tsx](src/platform/BookPage.tsx)。问题详情、原件、章节和卡片详情按需读取，书架不加载整书正文。

## 交互约定

- SSE 使用游标恢复，事件局部更新查询缓存；事件版本防止旧结果覆盖新状态。
- 内容确认响应提供下一问题，并预取所需数据；无需等待整书重算或固定轮询。
- 保存期间保护当前编辑范围；失败保留输入，草稿保存不等于放行。机器可在独立核验后自动写回，人工只处理剩余问题。
- 章节结构不额外要求人工批准。卡片经机器核验自动采用；未通过的候选不能显示为已采用成果。
- 列表按游标继续获取元数据，避免固定上限隐藏卡片或待办。
- 原件、页码或精确范围不足时明确说明，不用推测位置冒充引文出处。

## 契约与检查

`npm run contracts` 使用 [export-contracts.ts](scripts/export-contracts.ts)，先运行现役 FastAPI 的导出器，再生成 [schema.d.ts](src/platform/schema.d.ts)。契约工具依赖独立锁定，应用与工具的 TypeScript 版本不需要混装。修改接口时同时提交后端 OpenAPI 与前端类型。

```powershell
npm run typecheck
npm run check-unused
npm test
npm run build
```

Knip 检查生产入口及契约导出脚本；`tw-animate-css` 由 CSS 导入，因此保留显式例外。Vitest 测试与对应组件放在同一目录。浏览器内容验收仍需真实原件与实际任务，组件测试不替代整书验收。

旧 `docs/` 内的草案契约、页面设计和评审记录仅作本地历史资料，当前运行不读取它们。
