# 历史研究工作台 V2

React 19、React Router、TanStack Query 和生成的 openapi-fetch 客户端共同构成书籍工作台。主入口是书籍、待办、史料卡；每本书内包含进展、连续 Markdown 阅读、内容核对、检索和实际任务关系图。

使用 Uppy + tusd + S3 上传；Tiptap 编辑 Markdown；React Flow + Dagre 显示实际主/子 Agent、组件及工具节点。节点从运行记录产生，不固定数量或层级。编辑器和关系图按需加载。

## 运行和验证

部署入口见 [V2 后端说明](../research-platform/README.md)。生产构建由 `deploy/platform/Dockerfile.web` 提供，同源代理 `/api/v2` 和 `/uploads`。`/` 与 `/platform.html` 使用相同入口。

~~~powershell
npm ci
npm run contracts
npm run typecheck
npm run check-unused
npm test
npm run build
~~~

`contracts` 从 Python 的现役 FastAPI 导出 OpenAPI，再通过单独锁定工具环境生成 TypeScript；先安装 V2 Python 环境，并在 `tooling/openapi` 执行 `npm ci`。本地开发 `npm run dev` 使用 18156 端口，请避免与运行中的生产 Web 同时占用该端口。

`check-unused` 检查现役生产入口及契约导出脚本的未使用代码与依赖。Knip 的唯一依赖例外 `tw-animate-css` 实际由 `src/platform/theme.css` 的 CSS `@import` 加载；不是未使用依赖。旧问题锚点编辑接口与图筛选代码已存档撤除，现役核对编辑器直接编辑当前问题范围。

## 交互规则

书架只取元数据；章节正文、问题详情、卡片详情和原件按需获取。后台事件使用 SSE 游标恢复，局部更新 TanStack Query；预取下一问题，审核响应立即带回下一项，不等待整书重算。保存期间锁住问题切换；失败保留编辑内容。列表自动请求后续元数据页，避免卡片或待核对项被固定上限隐藏。

逐项确认必须由用户操作。模型修订保留为待核对提案，保存草稿不放行。文章结构不单独要求人工批准；卡片机器核验未通过时不可显示为可用成果。

旧前端源文件已逐文件存档并撤除，见 `output/refactor-v2/retired-frontend-archive.json` 和 `retired-frontend-files.json`。历史页面文档不作为现役路由或人工审核规则。
