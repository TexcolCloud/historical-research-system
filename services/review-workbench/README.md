# Review Workbench

以书籍任务为中心的 React 工作台，提供上传、处理进展、人工核对、阅读、原书目录、检索和导出。默认通过统一平台连接原生 Codex 持久队列；历史资料与卡片保留读取兼容，当前流程不生成新卡片。

[项目快速开始](../../README.md#快速开始) · [统一平台](../research-platform/README.md) · [API 契约](../research-platform/openapi.json)

## 启动与开发

完整系统的安装、配置和启动见项目快速开始。默认主机启动器将本模块的 `dist/`、`/api/v2` 和上传代理放在同一来源 `http://127.0.0.1:18170`，不需要另开 Vite。启动器的 `--build` 会执行前端构建，前提是已安装 Node.js 24+ 和本模块依赖。

开发前端需要 Node.js 24+，以及已配置、可访问的统一平台 API 与 tus 上传服务。以下命令均在项目根目录执行：

```powershell
npm --prefix services/review-workbench ci
npm --prefix services/review-workbench run dev
```

Vite 监听 `http://127.0.0.1:18156`，端口被占用时直接报错。可用 `npm --prefix services/review-workbench run dev -- --port 18157` 指定其他端口。

[vite.config.ts](vite.config.ts) 从项目根目录读取环境配置，并在开发和预览模式代理以下路径：

| 浏览器路径 | 默认目标 | 覆盖变量 |
| --- | --- | --- |
| `/api/v2` | `http://127.0.0.1:18170` | `WORKBENCH_API_ORIGIN` |
| `/uploads/` | `http://127.0.0.1:18171` | `WORKBENCH_UPLOAD_ORIGIN` |

上传也可经主机平台的同源代理转发，此时将 `WORKBENCH_UPLOAD_ORIGIN` 设为 `http://127.0.0.1:18170`。前端开发服务器不会启动后端、上传服务或处理任务。

保留的 Docker Web 部署使用 [Dockerfile.web](../../deploy/platform/Dockerfile.web) 构建静态文件，由 [Nginx](../../deploy/platform/nginx.conf) 在容器内监听 8080，并代理 API 和 tusd；外部 Web 端口由 Compose 配置。它属于显式 `--legacy` 部署路径，与默认主机平台部署分开。两个 HTML 构建入口 `/` 与 `/platform.html` 使用同一 React 应用。

## API 契约

客户端 [api.ts](src/client/api.ts) 使用 `openapi-fetch`，类型来自生成的 [schema.d.ts](src/client/schema.d.ts)。普通前端开发可使用仓库内的已生成类型；修改接口时再重新生成。

[export-contracts.ts](scripts/export-contracts.ts) 先运行统一平台的 `hrs_platform.export_openapi`，覆写 `services/research-platform/openapi.json`，再生成前端类型。执行前需完成统一平台 Python 环境安装和配置，使 `Settings.load()` 能读取所需数据库与对象存储配置；导出器不启动 API 服务。

脚本通过 `UV_PROJECT_ENVIRONMENT` 选择 Python 环境；请继承根 README 安装时使用的设置，新终端需重新设置同一变量。未设置时脚本查找 `services/research-platform/.venv`，不会自动查找根快速开始中的缓存环境。

契约工具有独立锁文件，保持其 TypeScript 依赖与应用隔离。在项目根目录执行：

```powershell
npm --prefix services/review-workbench/tooling/openapi ci
npm --prefix services/review-workbench run contracts
```

接口变更应同时提交后端 OpenAPI 和前端类型，避免手工编辑生成文件。

## 代码职责

| 位置 | 职责 |
| --- | --- |
| [main.tsx](src/main.tsx) | 创建根节点、QueryClient 和 Router provider |
| [app/](src/app/) | 路由装配、错误边界和共享外壳；保留上传会话及书架搜索、筛选条件 |
| [routes/](src/routes/) | 书架、单书和待办页面，按需加载功能 |
| [features/](src/features/) | 上传、书籍管理、内容核对、阅读、目录、搜索、运行进展与执行图 |
| [hooks/](src/hooks/) | 工作区 SSE 订阅、断线刷新及查询缓存更新 |
| [client/](src/client/) | 类型化 API 客户端、列表分页与缓存键 |
| [components/](src/components/)、[editor/](src/editor/)、[styles/](src/styles/) | 公共界面、执行关系图、Markdown 渲染编辑和样式 |

路由使用 React Router，服务端状态使用 TanStack Query；上传使用 Uppy、Golden Retriever 和 tus 协议；Markdown 编辑器使用 Tiptap 并按需加载；执行图使用 React Flow 与 Dagre。功能通过客户端访问后端，正文、原件、问题详情与执行历史按需读取。

## 交互约定

- 单书工作区分为处理进展、人工核对和阅读资料；正文与目录待办共享人工核对入口，原书目录和检索归入阅读资料。
- 上传会话随共享外壳存活，页面切换保留进行中的上传；上传支持恢复，成功后提供处理进展入口。
- 内容核对逐项对照原图，明确选择保留或修改。草稿保存完成后才能继续；全部疑点处理后仍需提交本页终审。离开页面保存草稿不等于内容放行。
- 默认编辑当前疑点，整页可视化和 Markdown 编辑作为次级操作；机器建议需人工选择，原件、脚注及修改记录保留。没有可靠局部定位时展示整页核对。
- 目录候选预览不产生决定。核对标题、日期、正文开头与原图后确认跳转；也可搜索标题、按 PDF 页序定位，或明确保留目录文字而不设置跳转。目录决定不构成正文批准，PDF 页序与书内页码分别显示。
- 提交使用持久回执与幂等决定标识；失败保留输入，证据版本变化时提示重新载入。成功回执可直接切换下一项，后台同步状态单独显示。
- SSE 由共享外壳统一订阅，先读取快照和游标，再接续事件；健康连接不额外轮询，断线时只刷新可见页面的活动查询。执行诊断按阶段分页加载，图只绘制视口内节点。
- 未入库资料可阅读底稿，检索提示索引尚未建立。修订待复核时继续读取已发布版本；原件或精确范围不足时明确显示缺失信息。

## 检查与构建

安装本模块依赖后，在项目根目录执行：

```powershell
npm --prefix services/review-workbench run typecheck
npm --prefix services/review-workbench run check-unused
npm --prefix services/review-workbench test
npm --prefix services/review-workbench run build
```

`build` 先执行 TypeScript 检查，再输出 `services/review-workbench/dist/`。`npm --prefix services/review-workbench run preview` 可在 18156 端口预览已有构建，仍依赖上述 API 和上传代理。

Vitest 测试与功能同目录；Knip 检查生产入口和契约脚本，CSS 导入的 `tw-animate-css` 配有显式例外。组件测试和构建验证界面实现；整书内容验收仍须使用真实原件与完整处理任务。
