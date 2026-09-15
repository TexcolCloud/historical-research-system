# Historical Research System

当前生产入口是 [Research Platform V2](services/research-platform/README.md)：围绕书籍提供上传、OCR、原件视觉核验、人工核对、连续阅读、检索和动态 Agent 制卡。前后端以同一业务接口和持久任务协作。

- [部署、启停和运行检查](services/research-platform/README.md)
- [前端工作台](services/review-workbench/README.md)
- [冻结的文档提取模块](services/document-extraction/README.md)

根 `docs/`、历史调优档案和评测数据按项目要求仅保留在开发主机，不随 GitHub 基线分发；模块文档中指向这些目录的链接是本地追溯入口。代码、依赖锁、部署脚本、数据库迁移、API 契约和必要测试资源随仓库保存。原书、数据库、S3 数据、模型权重和真实配置需要独立准备。

Windows GPU 主机运行隔离的 OCR/视觉 worker；Docker 管理 Web、FastAPI、CPU worker、Temporal 和 tusd。复用已有 PostgreSQL、SeaweedFS S3 和 OpenSearch 容器，但业务使用独立 V2 数据库、对象前缀与索引。旧业务文件不迁移、不读取。

首次部署使用 `.env.platform.example` 准备根 `.env`，已有配置不要覆盖。实际密钥只写入被忽略的 `.env`。原提取模块的独立参数仍参考 `.env.example` 和其模块文档；模型权重保持原布局。部署命令以 V2 README 为准，不再使用 `scripts/hrs.py up` 启动旧模块组合。

~~~powershell
$env:UV_PROJECT_ENVIRONMENT="$PWD/.cache/engineering-envs/research-platform"
uv sync --project services/research-platform --locked --group dev --extra retrieval
.cache/engineering-envs/research-platform/Scripts/python.exe scripts/hrs_v2.py init
.cache/engineering-envs/research-platform/Scripts/python.exe scripts/hrs_v2.py up --build
~~~

网页默认 http://127.0.0.1:18156/。人工只处理机器标出的内容问题；草稿保存不代表放行。整书未完成必需核对前，不入库或分块。史料卡保留文本与本地视觉机器核验，核验通过后自动采用。

整体交付验收仍在进行。实际书籍、浏览器续传及完整恢复的状态记录在 `output/refactor-v2/`；通过单元测试或技术样本不等于整书验收完成。历史调优文档保留为追溯资料，不代表现役部署方式。

已移除退役独立服务的评测脚本、构建入口、旧 API 契约及孤立测试；删除前版本可在 Git 提交 `b8bab4a` 中追溯。旧模块目录保留历史说明、验收资料和冻结评测资源。现役回归与 Ragas 工具位于 `services/research-platform`。

两个旧路径仍用于管理当前共享基础设施，保留原 Compose 项目名与卷定义：

- `services/document-ingestion/config/compose.acceptance.yml`：PostgreSQL、S3，及同目录初始化 SQL。
- `services/document-retrieval/config/compose.yml`：OpenSearch 与快照卷。

这些文件仅提供基础设施，不启动已退役的业务服务。迁移 Compose 管理路径前需要另行验证卷归属与备份恢复；本次清理不操作运行中的容器或业务数据。
