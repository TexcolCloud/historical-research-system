# 历史评测参考目录

本目录只说明历史参考材料的保留边界，不是运行时资源目录。返回 [项目 README](../../README.md)。

`normalization/` 中的旧 OpenCC 参考字典已经从 Git 移除，仅在已有机器上本地保留，供历史结果复现；新检出仓库不包含这些文件，也不需要补齐它们。它们不是默认 Codex 流程、现役服务或当前检索配置的依赖。

旧平台检索通过安装的 OpenCC 包及其 `t2s` 配置执行文本规范化，具体见 [检索实现](../../services/research-platform/src/hrs_platform/services/retrieval/compute.py)。不要把本地历史字典复制到现役配置。项目默认 OCR 与审核见 [Codex 流程](../../services/codex-pipeline/README.md)。

当前仍随 Git 跟踪的评测脚本、依赖声明和锁文件位于 [Ragas 工具目录](../../services/research-platform/evaluation/tools/ragas/README.md)。其输入、运行前提、当前依赖限制、输出和测试均由该 README 说明；这里没有独立 CLI、安装或测试步骤。

历史字典、验收报告、题集和史料摘录保持本地，不提交 Git。历史参考或已有评测结果不能作为新资料通过审核的依据。
