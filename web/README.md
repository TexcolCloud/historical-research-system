# Web Workbench

React 工作台提供书架上传、处理进展、人工核对、全文阅读、目录、检索和 Markdown 导出。网页与独立 MCP 使用同一处理队列；历史卡片保留读取和导出。

产品介绍位于 `/#/welcome`，展示原件对照、阅读与检索示例；“开始本机试用”进入真实书库。原有 `/#/`、书籍深链及 `/platform.html` 入口保留，产品介绍与书库切换不会销毁上传会话。

[项目快速开始](../README.md) · [API 契约](openapi.json)

## 启动与开发

完整平台由仓库根目录的统一入口启动，构建后的页面、API 和上传代理共用 `http://127.0.0.1:18170`。前端开发需要 Node.js 24+ 和已启动的平台 API、上传服务。以下 PowerShell 命令均从仓库根目录执行：

```powershell
npm --prefix web ci
npm --prefix web run dev
```

Vite 使用 `http://127.0.0.1:18156`，端口占用时直接报错。指定其他端口可执行 `npm --prefix web run dev -- --port 18157`。

[vite.config.ts](vite.config.ts) 从仓库根目录读取开发配置，代理 API 与上传：

| 浏览器路径 | 默认目标 | 可选覆盖 |
| --- | --- | --- |
| `/api/v2` | `http://127.0.0.1:18170` | `WORKBENCH_API_ORIGIN` |
| `/uploads/` | `http://127.0.0.1:18171` | `WORKBENCH_UPLOAD_ORIGIN` |

需要由平台同源代理处理上传时，将上传目标设为 `http://127.0.0.1:18170`。Vite 不启动后端或文档处理。两个 HTML 入口使用同一 React 应用，保留已有页面书签。

## API 契约

[api.ts](src/client/api.ts) 使用 `openapi-fetch`，类型来自生成的 [schema.d.ts](src/client/schema.d.ts)。后端接口变更时，从根目录生成契约：

```powershell
npm --prefix web/tooling/openapi ci
npm --prefix web run contracts
```

[export-contracts.ts](scripts/export-contracts.ts) 调用统一命令 `uv run --no-sync --locked python -m hrs contracts`，导出 `web/openapi.json`，再生成客户端类型。执行前需按根 README 准备根 Python 环境；导出不启动 API、模型或文档处理任务。

契约工具独立锁定 TypeScript 5 的生成器依赖，应用使用 TypeScript 7。接口变更同时提交 OpenAPI 与客户端类型，不手工修改生成文件。

## 代码职责

| 位置 | 职责 |
| --- | --- |
| [app/](src/app/) | 路由装配、错误边界、共享外壳与上传会话 |
| [routes/](src/routes/) | 书架、单书和待办入口 |
| [features/](src/features/) | 上传、书籍、核对、阅读、目录、搜索与进展 |
| [hooks/](src/hooks/) | 工作区 SSE、断线刷新和查询缓存更新 |
| [client/](src/client/) | 类型化 API、分页与缓存键 |
| [components/](src/components/)、[editor/](src/editor/)、[styles/](src/styles/) | 共享界面、Markdown 编辑阅读与样式 |

React Router 管理路由，TanStack Query 管理服务端状态；Uppy/tus 提供可恢复上传，Tiptap 编辑 Markdown，React Flow 展示执行关系。正文、原图、问题与执行详情按需加载。

样式按职责组织：`theme.css` 提供语义令牌和本地字体，`workbench.css` 拥有外壳、书籍与进展，`review-evidence.css` 拥有正文核对，`reader-navigation.css` 拥有目录、阅读和检索，`welcome.css` 仅随产品介绍加载。素材与字体许可见 [assets/README](src/assets/README.md)。

## 交互约定

- 上传成功后进入已有处理任务，不重复导入；切换页面保留正在上传的文件。
- 核对逐项对照原图，选择保留或修改；全部疑点处理后提交本页。草稿保存不表示内容通过。
- 当前疑点编辑为主要操作，整页编辑为次级操作；没有可靠位置时明确显示整页核对。
- 目录预览不提交决定。用户核对正文起点后确认跳转，也可保留目录文字而不设置跳转；物理页序与印刷页码分别显示。
- 提交失败保留输入，版本变化提示重新载入；成功回执与后台同步状态分开显示。
- SSE 先读取快照与游标再接续事件；健康连接不额外轮询，断线只刷新可见的活动查询。
- 未发布资料可查看底稿。修订待核验时继续读取已有发布版本，不能把候选当作已批准正文。

## 检查与构建

```powershell
npm --prefix web run typecheck
npm --prefix web run check-unused
npm --prefix web test
npm --prefix web run build
```

`build` 先检查 TypeScript，再生成 `web/dist/`。`npm --prefix web run preview` 在 18156 预览已有构建，仍依赖平台 API 和上传服务。

测试与功能代码同目录，组件测试与构建不代替真实史料验收；完整处理、原图核验、目录与脚注、检索及导出由项目端到端验收覆盖。
