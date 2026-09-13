# 完整首版验收 — 0.1.0

2026-09-08：**完整首版及最新代码的集成复验完成，待用户最终验收。**
现役机器证据为 [acceptance-summary.json](../evaluation/v1-recheck-20260908/acceptance-summary.json)，包含代码、锁文件、原件及报告 SHA-256。
[2026-09-07 原验收基线](../evaluation/v1-20260907/acceptance-summary.json)保持不变；新汇总明确区分本轮重跑与承接的旧证据。

同日环境配置统一至项目根目录 `.env` / `.env.example`，生产源码未变化。
[配置迁移验证](../evaluation/env-unification-20260908/configuration-verification.json)记录 15 项定向测试通过及更新后的测试/验收脚本哈希；
上述集成验收文件保留生成时快照，不将其辅助脚本哈希冒称为迁移后的当前值，也不将配置验证算作全量重跑。

## 已交付

输入准备 → 标准来源入库 → 自动规则草案 → 在线/离线编辑 → 依赖组预览与采用 → 固定快照/用途核查 → 下游基准与增量事件。
全流程共用 HTTP 和 PostgreSQL 持久任务；薄 CLI 只调用同一接口。没有复写提取模块、额外任务平台或向量系统。

- 规范目录、ZIP64、tus 断点续传，真实本地/S3 保存、完整回读、媒体 Range 和可恢复写入。
- 文献/历史版本/收录实例、书目多说法及采用理由、身份合并/拆分/撤回、复杂目录/附属归属、缺口和跨版本阅读补配。
- 规则子任务去重且不回滚父导入；按需 DeepSeek、持久包预算、模型配置隔离缓存；所有候选默认未批准。
- 草案历史、离线清单导回、待定/拒绝/重开、依赖组原子与部分采用、失效执行拒绝及重放恢复。
- 当前用途限制和固定来源分离，固定全量基准及可重放的已提交事件，管理查询/分页和三份机器契约。

## 实测结果

| 验收通道 | 结果 | 证据 |
| --- | --- | --- |
| 核心全量（含真实样本） | 本轮 90 passed，0 skipped，1041.55 秒 | [core.xml](../evaluation/v1-recheck-20260908/core.xml) |
| 独立 tus + 小包/5 GiB，两后端 | 承接 09-07：4 passed，0 skipped，975.36 秒；本轮未重跑 | [large.xml](../evaluation/v1-20260907/large.xml) |
| DeepSeek 工程文本实调用/缓存 | 承接 09-07：1 passed，0 skipped；本轮未实调用 | [live.xml](../evaluation/v1-20260907/live.xml) |
| Linux 原生 CLI/中文路径/双后端/重启 | 使用当前源码重建镜像，重新全链路通过 | [本地](../evaluation/v1-recheck-20260908/linux-local/runtime.json)、[S3](../evaluation/v1-recheck-20260908/linux-s3/runtime.json) |
| OpenAPI、包装及草案 Schema | 导出与运行时一致；另独立复跑 2 passed | [contracts.xml](../evaluation/v1-recheck-20260908/contracts.xml) |
| Ruff / format / Python 编译 | 通过；87 个 Python 文件格式检查通过 | [quality.json](../evaluation/v1-recheck-20260908/quality.json) |

本轮新执行核心 90 项，定向分篇 17 项和独立契约 2 项均已包含在核心中，不重复计数；另加 Linux 两个完整工作流。
承接的大包 4 项与模型实调用 1 项有独立旧报告，汇总中的 95 项有效覆盖不表示本轮新跑了 95 项。
保留两条上游弃用警告：Starlette 的 httpx 兼容路径与 AnyIO BlockingPortal 别名；没有静默过滤。

### 集成复验说明

- 验收后变化仅在 `partitioning.py`：显式 `article_id=null` 不再回退版面分组；`contents`、`parallel-abstract` 保留待定归属；没有 `article_id` 字段的旧输入仍兼容版面提示。
- 新增 8 个 HTTP/worker/PostgreSQL 双存储用例。对照旧验收镜像的原源码，6 个修正场景按预期失败、2 个兼容场景通过；当前源码定向 17 项全部通过。见[旧版敏感性检查](../evaluation/v1-recheck-20260908/ownership-baseline-red.xml)与[当前定向回归](../evaluation/v1-recheck-20260908/ownership-green.xml)。旧版的预期失败不是当前回归失败。
- 核心运行前后源码相同，且与汇总及 Linux 镜像内容一致；见[运行绑定](../evaluation/v1-recheck-20260908/core-runtime.json)。其他生产源码、模型调用语句、Python/依赖版本及锁文件与原基线相同，经校验后才承接未受影响的传输和实调用证据。
- 本轮未修改生产源码、提取输出或生产模型策略；补充了回归测试、证据绑定和验收文档，不追加迁移，也不追改已经保存的旧草案。

以下大包数值来自 2026-09-07 的实测记录，不是本轮重新测得的性能数据。
真实 ZIP64 包 5,368,709,751 字节，成员 5,368,709,121 字节，实际写入而非稀疏占位。
包和成员都超过 4 GiB；独立 `tus-js-client 4.3.1` 在 >4 GiB 确认前缀处停止，
重启 HTTP/worker/客户端后沿原上传 URL 继续，未从头另建会话。

| 后端 | HTTP / worker 峰值内存 | 准备耗时 | 全程耗时 |
| --- | --- | --- | --- |
| 本地 | 约 167 / 96 MiB | 37.95 秒 | 169.11 秒 |
| S3 | 约 195 / 133 MiB | 180.00 秒 | 788.47 秒 |

内存统计包括 Windows venv 启动器及真实解释器子进程；不是只测启动器。
Range 覆盖首/中/尾及跨 4 GiB 边界；S3 实际 206 响应 Content-Length=128，不下载整件再截取。
专用 SeaweedFS 容器重启且保留原卷后，原资产完整 SHA-256 再核验通过。
详见[本地大包](../evaluation/v1-20260907/transport-local-large.json)与 [S3 大包](../evaluation/v1-20260907/transport-s3-large.json)。

## 真实材料与审阅范围

五页包保留 11 个固定来源范围、50 项 needs-evidence、3 项 generated-not-source。
九页包保留 70 个来源范围、2 次图片收录、106 条表格引用、142 项 needs-evidence 和 10 项 generated-not-source。
两份材料均在本地文件存储和**本机回环地址的专用 S3 容器**完成标准导入、草案采用、基准及事件检查；没有向云端上传。

[本轮 GPT 原件优先审阅](../evaluation/v1-recheck-20260908/gpt-original-first-review.json)先看原 PDF，再比对图表、候选与回执，并保存证据摘要。
五页原件可见题名为“抗战时期党在湖北的统战工作及其历史启示”，与候选一致；旧机器审阅误读为“统一战线”，本轮撤销该措辞差异误报，保留旧报告供追溯。
九页样本的临时候选题名仍混入英文，继续待核对；可通过现有书目断言/草案接口补充原件依据。
可见题名一致不等于正式书目批准；入库保真通过不等于 OCR 或史实正确。
工程图片、构造关系和大文件只证明协议与业务行为；模型实调用只发送明确构造的短文本。

## 运行与交接

- 运行说明：[README](../README.md)；完整调用/恢复：[usage.md](usage.md)；HTTP 合同：[api.md](api.md)。
- 开发库已显式升级至 `0014_assistance_budget`，再次升级不变。Linux 镜像 ID 和固定依赖在验收汇总中。
- 本模块新增文件尚未提交 Git；未暂存、提交或改动提取模块的既有工作。当前结果是本地实现/验收，不是公网生产部署。
- 原件、输入、历史失败报告、测试工作文件与持久卷保留供复核，未自动清场；临时测试数据库按夹具只删除测试自己创建的库。
- 历史 `large-transport.xml` 中一次旧迁移 worker 重启拒绝不是当前失败；最终结果以 large.xml 为准。Linux 复跑的旧输出冲突已用每次新建工程目录解决，旧文件仍保留。

后续范围：统一前端、生产认证/多租户、云厂商验证、向量/制卡/LangGraph，以及所有模块完成后的项目级边界检查。
用户最终接受、材料人工审定和 sealed gold 尚未进行；没有以机器审核代签。
