# Shared Runtime

转换和统一平台共用的 Python 包 `hrs-runtime`。它不拥有业务数据库，也不提供独立 HTTP 服务。

[项目入口](../../README.md) · [平台配置](../../services/research-platform/README.md#运行与配置) · [模型安装](../../models/README.md)

## 提供的能力

| 模块                                                   | 职责                                                        |
| ------------------------------------------------------ | ----------------------------------------------------------- |
| [object_storage.py](src/hrs_runtime/object_storage.py) | boto3 S3 内容寻址写入、摘要与长度校验、临时缓存恢复         |
| [local_vision.py](src/hrs_runtime/local_vision.py)     | 本地 Qwen 调用、响应模型／完整性检查、OCR 与视觉共用 GPU 锁 |
| [page_layout.py](src/hrs_runtime/page_layout.py)       | 页码与来源布局共用规则                                      |
| [review_scope.py](src/hrs_runtime/review_scope.py)     | 审核修订范围的共用定位规则                                  |

S3 调用方显式传入 endpoint、bucket、凭据和 region；业务模块负责事务与对象引用。缺配置或校验失败会报错，不回退到本地业务文件。旧的环境存储工厂、HTTP 诊断／鉴权中间件及关联 ID 包装已退役，不属于此包接口。

## 接入与验证

平台和 OCR 的锁定依赖引用本地包；随仓库一起安装，不假设公共包仓库存在相同实现。安装方式见项目快速开始。

GPU 锁和转换环境共用项目根目录解析：优先使用 `HRS_PROJECT_ROOT`，源码安装则向上查找 `.env.platform.example` 与 `services/`，不依赖个人指令文件。视觉服务默认仅访问回环地址，通过主机启动器管理。

在项目根目录、已准备专用测试环境后执行：

```powershell
.cache/engineering-envs/research-platform/Scripts/python.exe -m pytest packages/runtime-support/tests -q
```

S3／模型真实集成与 GPU 调度检查参见 [平台验证](../../services/research-platform/README.md#开发参考与验证)；单元检查不代表真实模型推理验收。
