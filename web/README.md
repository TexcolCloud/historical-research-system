# Web Workbench

React 工作台提供书架上传、处理进展、人工核对、全文阅读、目录、结构化史料卡、人物事件及材料关系、联合检索、研究问题选材和 Markdown 导出。网页与独立 MCP 使用同一处理契约；旧 `kind=cards` 历史卡片保留读取和导出，新研究使用独立任务队列。

界面面向单人桌面使用，最低布局宽度为 1080px；`/#/` 直接进入书籍，书籍深链及 `/platform.html` 入口保留。全库研究入口为 `/#/research`，单书史料卡使用 `/#/books/:bookId?tab=research`；`card`、`entity` 查询参数保留详情深链，`researchRun` 定位具体研究任务。`/#/research?view=questions` 整理研究问题、选材与笔记。书籍、核对、阅读与研究检索之间切换不会销毁上传会话。

[项目快速开始](../README.md) · [API 契约](openapi.json)

## 启动与开发

完整平台由仓库根目录的统一入口启动，构建后的页面、API 和上传代理共用 `http://127.0.0.1:18170`。前端开发需要 Node.js 24+ 和已启动的平台 API、上传服务。以下 PowerShell 命令均从仓库根目录执行：

```powershell
npm --prefix web ci
npm --prefix web run dev
```

Vite 使用 `http://127.0.0.1:18156`，端口占用时直接报错。指定其他端口可执行 `npm --prefix web run dev -- --port 18157`。

[vite.config.ts](vite.config.ts) 从仓库根目录读取开发配置，代理 API 与上传：

| 浏览器路径  | 默认目标                 | 可选覆盖                  |
| ----------- | ------------------------ | ------------------------- |
| `/api/v2`   | `http://127.0.0.1:18170` | `WORKBENCH_API_ORIGIN`    |
| `/uploads/` | `http://127.0.0.1:18171` | `WORKBENCH_UPLOAD_ORIGIN` |

需要由平台同源代理处理上传时，将上传目标设为 `http://127.0.0.1:18170`。Vite 不启动后端或文档处理。两个 HTML 入口使用同一 React 应用，保留已有页面书签。

## API 契约

[api.ts](src/client/api.ts) 使用 `openapi-fetch`，研究接口的 [api.ts](src/features/research/api.ts) 使用同源 `fetch` 并传递取消信号；两者类型均来自生成的 [schema.d.ts](src/client/schema.d.ts)。后端接口变更时，从根目录生成契约：

```powershell
npm --prefix web/tooling/openapi ci
npm --prefix web run contracts
```

[export-contracts.ts](scripts/export-contracts.ts) 调用统一命令 `uv run --no-sync --locked python -m hrs contracts`，导出 `web/openapi.json`，再生成客户端类型。执行前需按根 README 准备根 Python 环境；导出不启动 API、模型或文档处理任务。

契约工具独立锁定 TypeScript 5 的生成器依赖，应用使用 TypeScript 7。接口变更同时提交 OpenAPI 与客户端类型，不手工修改生成文件。

## 代码职责

| 位置                                                                           | 职责                                                    |
| ------------------------------------------------------------------------------ | ------------------------------------------------------- |
| [app/](src/app/)                                                               | 路由装配、错误边界、共享外壳与上传会话                  |
| [routes/](src/routes/)                                                         | 书架、单书、待办和全库研究入口                          |
| [features/](src/features/)                                                     | 上传、书籍、核对、阅读、目录、搜索、研究卡片/实体与进展 |
| [hooks/](src/hooks/)                                                           | 工作区 SSE、断线刷新和查询缓存更新                      |
| [client/](src/client/)                                                         | 类型化 API、分页与缓存键                                |
| [components/](src/components/)、[editor/](src/editor/)、[styles/](src/styles/) | 共享界面、Markdown 编辑阅读与样式                       |

React Router 管理路由，TanStack Query 管理服务端状态；Uppy/tus 提供可恢复上传，Tiptap 编辑 Markdown，React Flow 展示执行关系及有限范围的研究证据关系。正文、原图、问题、研究与执行详情按需加载；研究清单分页读取摘要，原图、关系与事项时间线由用户展开后读取。

样式按职责组织：`theme.css` 提供语义令牌和本地字体，`workbench.css` 拥有外壳、书籍与进展，`review-evidence.css` 拥有正文核对，`reader-navigation.css` 拥有目录、阅读和检索，`research.css` 拥有研究卡片和人物事件详情。字体许可见 [assets/README](src/assets/README.md)。

## 交互约定

- 上传成功后进入已有处理任务，不重复导入；切换页面保留正在上传的文件。
- 核对逐项对照原图，选择保留或修改；全部疑点处理后提交本页。草稿保存不表示内容通过。
- 当前疑点编辑为主要操作，整页编辑为次级操作；没有可靠位置时明确显示整页核对。
- 目录预览不提交决定。用户核对正文起点后确认跳转，也可保留目录文字而不设置跳转；物理页序与印刷页码分别显示。
- 提交失败保留输入，版本变化提示重新载入；成功回执与后台同步状态分开显示。
- 书籍 SSE 先读取快照与游标再接续事件；健康连接不额外轮询，断线只刷新可见的活动查询。独立研究任务仅在排队或运行中轮询轻量进度，停止、待人工或完成后停止轮询，卡片清单按新增结果或决定刷新。
- 未发布资料可查看底稿。修订待核验时继续读取已有发布版本，不能把候选当作已批准正文。
- 联合检索可指定多本书，分别展示原文、卡片、人物、事件与其他实体；原文及研究索引的就绪、构建、降级状态分别显示。
- 研究卡与原图可并排核对，支持选择引用、物理页和缩放；引用共用阅读器的版本哈希与精确位置，旧依据不能高亮新正文，返回链接保留检索和卡片上下文。正文中的人物事件链接仅用于当前版本中已验证的精确位置，相关卡片可在阅读侧栏查看。
- 完整卡片可修订编目、日期、内容条目、引用范围、人物事件与材料关系；修订创建新版本并重新独立核验，原有卡片和人工决定继续保留。未知元数据注明原因，不能用猜测填补。
- 人物身份提供同一、不同、拆分、别名及撤回别名的显式决定，引用双方当前来源版本。事件分别标记材料报告、计划、条件和否定，材料报告不表示已得到独立互证。材料关系可核对双方卡片和独立证据包。
- 正文目录与研究待办共用待办入口。原文核验通过与人工确认分别标记；待人工卡由用户采用、修订或排除，无需审核人姓名或意见。从研究待办提交成功后进入下一项，冲突及读取失败保留草稿和成功回执。
- 研究问题保留选材采用或排除、笔记、模型结论及未解事项。继续查证启动正常原生研究任务；自动处理停止后仍按实际任务标明待人工、暂停或技术阻塞。问题、身份和卡片提交均检查版本，后台进展不能覆盖本地输入。
- 旧研究流程的已完成卡片明确标为基础结果，可创建完整研究的新修订；旧任务和历史内容不会被就地改写。

## 检查与构建

```powershell
npm --prefix web run typecheck
npm --prefix web run check-unused
npm --prefix web test
npm --prefix web run build
```

`build` 先检查 TypeScript，再生成 `web/dist/`。`npm --prefix web run preview` 在 18156 预览已有构建，仍依赖平台 API 和上传服务。

测试与功能代码同目录，组件测试与构建不代替真实史料验收；完整处理、原图核验、目录与脚注、检索及导出由项目端到端验收覆盖。
