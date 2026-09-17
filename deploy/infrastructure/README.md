# Shared infrastructure

| 配置 | 服务 | 保留的 Compose 项目名 |
| --- | --- | --- |
| `compose.storage.yml` | PostgreSQL、SeaweedFS S3 | `historical-ingestion-acceptance` |
| `compose.search.yml` | OpenSearch、快照卷初始化 | `historical-retrieval` |

在仓库根目录使用根 `.env`：

```powershell
docker compose --env-file .env -f deploy/infrastructure/compose.storage.yml up -d --wait
docker compose -f deploy/infrastructure/compose.search.yml up -d --wait
```

仅管理路径改变，项目名、服务名、端口、命名卷及初始化 SQL 保持原样。已有部署不要更换项目名或使用 `down -v`；应用部署仍在 `deploy/platform/`。测试使用单独的 `deploy/platform/compose.test.yml`，不接触业务卷。完整初始化、凭据和备份说明见 [平台文档](../../services/research-platform/README.md)。
