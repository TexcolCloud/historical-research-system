# Local Infrastructure

完整平台由主机 Python 进程提供网页与 API；本目录只管理 PostgreSQL、S3、OpenSearch、tusd 和隔离测试存储。安装及启动见[项目 README](../README.md)。

## 配置与持久化

| 配置 | Compose 默认项目名 | 服务与主机端口 | 持久卷 |
| --- | --- | --- | --- |
| [compose.storage.yml](compose.storage.yml) | `historical-ingestion-acceptance` | PostgreSQL `55436`、S3 `58333` | `postgres-data`、`s3-data` |
| [compose.search.yml](compose.search.yml) | `historical-retrieval` | OpenSearch `19260` | `index-data`、`index-recovery` |
| [compose.upload.yml](compose.upload.yml) | `hrs-platform` | tusd `18171` | 上传对象写入 S3 |
| [compose.test.yml](compose.test.yml) | `hrs-platform-ci` | 隔离 PostgreSQL `55439`、S3 `58339`、OpenSearch `19269` | 不挂载业务卷 |

端口只绑定本机回环地址。各配置保留原项目名和卷名；`.env` 中已有的 `COMPOSE_PROJECT_NAME` 覆盖也须保留。不要通过更改项目名或 `down -v` 处理连接故障，它们会改变既有数据的归属。

根 `.env` 的数据库密码与 S3 凭据供存储和上传使用。默认桶为 `ingestion-acceptance`，region 为 `us-east-1`；默认地址、端口和数据库连接由统一配置推导。已有显式覆盖继续生效。

隔离验收可显式设置 `INGEST_S3_UPLOAD_ENDPOINT`、`PLATFORM_UPLOAD_PORT` 和 `PLATFORM_TUS_HOOK_URL`，同时使用独立 Compose 项目和匹配的测试桶／凭据，使上传与 API 指向同一隔离存储。默认部署无需填写这些覆盖项。

数据库初始化由 Python 启动入口管理，创建缺失的专用数据库并执行历史迁移。容器初始化密码只在空数据目录首次启动时应用，修改环境变量不会重置已有角色或数据库。

OpenSearch 是本机单节点配置，关闭安全插件，堆上限 4 GiB、容器内存上限 8 GiB。`index-recovery` 提供快照存储位置，但挂载目录不等于已完成备份。

## 维护

以下 PowerShell 命令从仓库根目录执行，用于查看当前服务：

```powershell
docker compose --env-file .env -f deploy/compose.storage.yml ps
docker compose --env-file .env -f deploy/compose.search.yml ps
docker compose --env-file .env -f deploy/compose.upload.yml ps
```

统一启动器的停止命令保留业务数据，不会清空共享基础设施。先停止应用与所有共享工作区的独立执行器，等待完全退出，再停止上传并完成备份；SQL/S3 在备份期间继续运行：

```powershell
uv run --no-sync --locked python -m hrs stop
# 等待主机应用及独立工作台/执行器退出后：
docker compose --env-file .env -f deploy/compose.upload.yml stop tusd
uv run --no-sync --locked python -m hrs backup
```

独立保存 S3 业务对象和私有配置后，再按维护需要停止存储和检索服务：

```powershell
docker compose --env-file .env -f deploy/compose.storage.yml stop
docker compose --env-file .env -f deploy/compose.search.yml stop
```

排障可在对应 Compose 命令末尾使用 `logs --tail 100`。核对既有角色密码、S3 桶和凭据、Docker 内存与磁盘空间；保留失败记录，不清空卷。分享日志前去除凭据与访问令牌。

## 隔离测试

CI 使用 `compose.test.yml` 和显式测试连接，禁止向测试挂载业务卷。测试命令始终指定项目名，避免已有 `COMPOSE_PROJECT_NAME` 覆盖测试项目身份：

```powershell
docker compose --project-name hrs-platform-ci -f deploy/compose.test.yml up -d --wait
docker compose --project-name hrs-platform-ci -f deploy/compose.test.yml down
```

本地运行测试也须按[工程 CI](../.github/workflows/engineering.yml)设置全部测试数据库、S3 和 OpenSearch 连接变量；单独启动测试容器不会改变应用连接目标。测试容器、模拟模型和构建成功不代表真实史料经过人工批准。
