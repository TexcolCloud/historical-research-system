# 共享基础设施

此目录提供完整 React 平台使用的 PostgreSQL、S3 与 OpenSearch。默认主机启动器的 `init` 和 `up` 会启动这些 Compose；独立 Codex MCP 工作区不依赖它们。首次安装见 [根 README 快速开始](../../README.md#快速开始)，配置及备份见 [平台说明](../../services/research-platform/README.md)。所有命令从项目根目录执行。

## 配置与数据

| 文件 | Compose 项目名 | 服务与主机端口 | 持久卷 |
| --- | --- | --- | --- |
| [compose.storage.yml](compose.storage.yml) | `historical-ingestion-acceptance` | PostgreSQL：`127.0.0.1:55436`；SeaweedFS S3：`127.0.0.1:58333` | `postgres-data`、`s3-data` |
| [compose.search.yml](compose.search.yml) | `historical-retrieval` | OpenSearch：`127.0.0.1:19260` | `index-data`、`index-recovery` |

表中的卷名由各 Compose 项目加前缀。保留项目名是为了继续使用既有数据；不要随意更名或使用 `down -v`。OpenSearch 的 `index-recovery` 为快照存储位置，配置挂载不等于已创建快照。SQL 与 S3 保存业务状态／对象，OpenSearch 索引可重建。

存储 Compose 从根 `.env` 读取 `INGEST_DEV_DB_PASSWORD`、`INGEST_S3_ACCESS_KEY`、`INGEST_S3_SECRET_KEY`，以及可选 `INGEST_DEV_DB_PORT`、`INGEST_S3_BUCKET`。填写 [配置模板](../../.env.platform.example) 的占位符，并与平台连接配置保持一致。PostgreSQL 初始化使用 [postgres-init.sql](postgres-init.sql)，仅在空数据目录首次初始化时执行；更改初始化 SQL 或密码环境变量不会重建现有数据库。

S3 主机端口固定为 `58333`，OpenSearch 固定为 `19260`。OpenSearch 是关闭安全插件的本机单节点配置，堆上限为 4 GiB、容器内存上限为 8 GiB；部署到其他边界前需另行配置。

## 维护命令

本页描述仓库默认的本机存储。自定义部署需同时调整启动器、平台连接和 tusd 上传目标；只改平台 S3 地址会导致上传与读取使用不同存储。以下命令可单独管理默认基础设施：

```powershell
docker compose --env-file .env -f deploy/infrastructure/compose.storage.yml up -d --wait
docker compose --env-file .env -f deploy/infrastructure/compose.search.yml up -d --wait
docker compose --env-file .env -f deploy/infrastructure/compose.storage.yml ps
docker compose --env-file .env -f deploy/infrastructure/compose.search.yml ps
```

默认主机启动器 `stop` 不停止这些服务。维护前先停止应用、上传和执行器，按 [平台备份与恢复](../../services/research-platform/README.md#备份与恢复) 保留协调 SQL／原生快照及独立 S3 备份。需要停止基础设施时使用 `stop` 保留容器及卷：

```powershell
docker compose --env-file .env -f deploy/infrastructure/compose.storage.yml stop
docker compose --env-file .env -f deploy/infrastructure/compose.search.yml stop
```

## 排障与隔离测试

| 问题 | 检查 |
| --- | --- |
| Compose 拒绝启动 | `.env` 是否仍含占位符、Docker Desktop 是否可用、端口是否被其他部署占用 |
| PostgreSQL 认证失败 | 平台连接用户名／密码与现有数据库角色是否一致；现有卷不重新应用初始化环境变量 |
| S3 无法访问 | endpoint、bucket、凭据与 `s3` 日志；不要通过清卷修复连接问题 |
| OpenSearch 不健康 | `opensearch` 与 `initialize-recovery` 日志、Docker 内存额度、数据卷权限和磁盘空间 |

查看日志可在对应 Compose 命令末尾使用 `logs --tail 100`。平台的完整依赖诊断见其 README。测试使用 [deploy/platform/compose.test.yml](../platform/compose.test.yml) 的独立项目、端口与数据，不将本目录的业务卷挂入测试。具体验证命令和环境变量见 [CI](../../.github/workflows/engineering.yml)。
