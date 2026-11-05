# 目录待办状态一致性修复

原问题可在实际书籍稳定复现：人工核对显示目录确认0项，阅读目录保留1条“未找到对应正文”。实时与发布目录的SHA、版本均相同，且全文覆盖完成，排除新旧快照差异。根因是平台投影和前端队列只统计 `conflict`，没有把 `not_found` 纳入人工待办。

## 统一口径

| 原始状态 | 处理方式 |
| --- | --- |
| conflict / not_found / 旧 unresolved | 需要人工确认 |
| waiting_evidence | 等待自动核验，不计人工待办 |
| matched / matched_manual | 已关联或已确认跳转 |
| unlinked_manual | 已确认不设置跳转，已结束 |

原生导航模块提供 `navigation_requires_confirmation` 与只读 `navigation_review_summary`。平台计数、实时/发布报告共用该分类；API派生 `needs_confirmation` 和 `pending_count`。前端的待办、筛选、续审及提交后计数使用API字段，不再单独判断候选冲突。

阅读提示区分人工待办、系统等待、人工明确不跳转及阅读投影缺口。人工确认不跳转后显示“已确认不设置跳转”，不会继续提示位置不确定。

独立的导航确认策略纳入投影输入与跳过判断，使旧SQL零计数在正常桥接中刷新。通用 `PROJECTION_POLICY` 不变，不重识别、不重建索引，也不撤销已经发布的正文。派生字段不写回原始导航，不改变导航SHA和人工提交的证据基准。

确认后的发布目录缓存随导航计数、发布及版本事件失效，重新读取其对应发布快照；不会以实时目录覆盖旧发布内容。普通进度事件不刷新阅读缓存。

## 本地验证

- 原生全量246 passed；平台/runtime-support/脚本459 passed、15 skipped；前端41文件138 passed。类型检查、check-unused、Ruff、构建与diff检查通过。
- 原症状API与网页先红后绿：书籍计数、实时目录、发布目录均由0/缺失统一为1；不带条目参数进入人工目录核对也自动打开未找到的条目。
- 旧SQL记录正常增量投影后计数0→1，书籍仍ready；原始导航完整快照及SHA、发布指纹、索引代际保持不变。225页证据、9条核对记录、9条人工决定、23篇文章记录保持不变。
- 状态矩阵与人工明确不跳转的终结行为通过。带永久新鲜缓存的实际QueryObserver分别验证导航计数、发布、版本更新事件能重新读取发布目录；其他书与实时目录没有被覆盖。
- 另一份独立48页真实材料中的155条waiting_evidence仍计为0个人工目录待办，3条matched保持完成；未将证据尚未齐全误报为人工任务。来源证据完整验证通过，没有代替人工裁决真实条目。

本地证据保留在 `output/navigation-status-20261106/`：`probe-before.json`、`real-red.log`、`real-green.log`、`browser-after.json`、`before.png`、`after.png`、三组全量测试日志、`projection-after.json`、`navigation-after.json`及`protected-final.log`。修复只统一状态与同步，尚未找到的目录仍由用户决定关联位置或明确不设置跳转。
