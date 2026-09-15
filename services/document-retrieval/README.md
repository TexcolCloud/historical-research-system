# document-retrieval（退役入口）

当前业务接口、运行和测试统一见 [Research Platform](../research-platform/README.md)。不要使用旧独立模块的命令或 API 草案启动业务。

保留 [config/compose.yml](config/compose.yml)，管理共享 OpenSearch 和快照卷；冻结的归一化字典继续保留。该目录不再提供旧检索服务。

历史设计、操作和验收资料仅保留在本地，不随 Git 分发。历史实现可从 [Git 快照](https://github.com/TexcolCloud/historical-research-system/tree/b8bab4a/services/document-retrieval) 追溯；这些记录不表示当前部署行为。
