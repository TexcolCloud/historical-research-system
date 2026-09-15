# 文档检索：退役模块记录

现役实现、API、运行方式和有效测试见 [Research Platform](../research-platform/README.md)。本目录不再提供独立业务服务。

已清理不能独立运行的旧评测脚本、旧 API 契约及构建入口。删除前文件可查看 [Git 快照 b8bab4a](https://github.com/TexcolCloud/historical-research-system/tree/b8bab4a/services/document-retrieval)；更早实现通过 Git 历史追溯。保留的历史文档描述当时的规则、路径和命令，不作为当前操作指南。

保留 `config/compose.yml`，用于当前仍在使用的 OpenSearch 与快照卷；冻结评测中的归一化字典保持原样。

本地被忽略的评测结果、原件与业务文件不在此次删除范围内；未迁移或清除数据库、S3 对象及模型权重。
