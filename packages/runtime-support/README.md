# Shared Runtime

`hrs-runtime` 是统一平台与保留的本地转换模块共享的 Python 包，提供对象存储、页面定位和本地视觉运行工具。它不拥有业务数据库，也不提供独立 HTTP 服务。原生 Codex 的 OCR 与复核由 Codex 子代理执行，本包的本地视觉接口属于兼容运行能力。

[项目快速开始](../../README.md#快速开始) · [统一平台](../../services/research-platform/README.md) · [本地转换](../../services/document-extraction/README.md)

## 模块与调用边界

| 模块 | 职责与约束 |
| --- | --- |
| [object_storage.py](src/hrs_runtime/object_storage.py) | `S3Objects` 通过 boto3 写入内容寻址对象，读取时校验 SHA-256 和长度；`materialize()` 将校验后的对象恢复到临时计算缓存 |
| [page_layout.py](src/hrs_runtime/page_layout.py) | `page_layout_exclusions()` 识别完整物理页边缘的独立数字页码，保留原 Unicode 坐标、文字摘要及规则版本；表格、脚注等受保护区域不作页码排除 |
| [review_scope.py](src/hrs_runtime/review_scope.py) | 唯一定位原文片段、拆分行内修改并计算疑点范围；允许保守的排版归一化匹配，歧义位置返回未定位 |
| [local_vision.py](src/hrs_runtime/local_vision.py) | 调用本地 Qwen 视觉服务、检查响应模型与完成状态，并提供 OCR 和视觉共用的跨进程 GPU 文件锁 |

S3 调用方必须显式传入 endpoint、bucket 和访问凭据，region 默认 `us-east-1`；缺少必要配置会报错，不回退到本地业务文件。业务模块负责事务和对象引用，缓存文件不替代对象存储。`read_bytes()` 仅接收不超过 128 MiB 的对象，更大对象使用 `materialize()` 流式恢复。

页面布局规则必须接收完整物理页，不能对搜索片段或正文分块调用。定位和修改范围使用当前原文坐标；段落换行变化按完整结构修改处理，由调用方保存原文、补丁和证据。

## 安装与配置

要求 Python 3.11+。统一平台和本地转换模块的 uv 配置均通过仓库内可编辑依赖引用本包，随对应服务的锁定环境安装；完整安装步骤见项目快速开始。本包不需要单独启动。

仅运行兼容本地视觉路径时需要下列配置：

| 变量 | 行为 |
| --- | --- |
| `HRS_PROJECT_ROOT` | 显式指定项目根目录；未设置时，从源码位置向上查找同时含 `.env.platform.example` 和 `services/` 的目录。安装到仓库外时需显式设置 |
| `HRS_LOCAL_VISION_URL` | 默认 `http://127.0.0.1:18160`；只接受无 URL 凭据的回环 HTTP 地址 |
| `HRS_GPU_LOCK` | GPU 锁文件路径，默认使用项目根目录下的 `state/local-vision/gpu.lock` |
| `HRS_TASK_ID` | 可选任务标识，作为视觉请求上下文；调用方也可设置 `task_scope` |

视觉响应必须来自 `Qwen3-VL-8B-Instruct-Q4_K_M` 且正常完成，否则调用报错。`gpu_lease()` 只管理进程间锁；调用方负责在锁内安排 OCR、视觉调用及取消检查，不能删除其他进程持有的锁文件。本地模型与兼容部署配置见[模型目录](../../models/README.md)和[统一平台](../../services/research-platform/README.md)。

## 验证

按项目快速开始准备统一平台开发环境，并继承其 `UV_PROJECT_ENVIRONMENT` 设置；新终端需重新设置同一变量。环境须包含统一平台的 `dev` 依赖组。在项目根目录执行：

```powershell
uv run --no-sync --project services/research-platform --locked python -m pytest packages/runtime-support/tests -q
```

本目录测试覆盖项目根目录解析和审核修改范围，不启动 S3、模型或 GPU 任务。对象存储和模型集成检查由调用服务负责，单元测试不代表真实模型推理或内容验收。
