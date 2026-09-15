# document-ingestion（退役入口）

当前业务接口、运行和测试统一见 [Research Platform](../research-platform/README.md)。不要使用旧独立模块的命令或 API 草案启动业务。

保留 [config/compose.acceptance.yml](config/compose.acceptance.yml) 和 [postgres-init.sql](config/postgres-init.sql)，管理共享 PostgreSQL 与 S3。项目名及卷定义仍沿用原值，迁移管理路径需另外核对卷归属。

历史设计、操作和验收资料仅保留在本地，不随 Git 分发。历史实现可从 [Git 快照](https://github.com/TexcolCloud/historical-research-system/tree/b8bab4a/services/document-ingestion) 追溯；这些记录不表示当前部署行为。
