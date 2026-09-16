# README 工程图

两张 SVG 是可直接编辑的图源。README 引用同一份文件；不另外维护 Mermaid 或位图副本，也不引入运行时绘图库。

- [书籍处理工作流](book-workflow.svg)：按阶段从左到右、阶段内部从上到下阅读。同名字母圆形连接符表示流程接续；矩形表示步骤或产物，菱形表示判断，分支直接标注条件。自动处理、人工处理和可用产物同时用文字与颜色区分。
- [系统组件与部署边界](system-architecture.svg)：展示当前单工作站的组件职责、运行边界和协议。实线表示请求方向，虚线表示任务或已提交事件的逻辑方向。D1–D3 是直接存储依赖，G1 是共享模型服务引用；不代表新增代理层。

连线统一使用水平、垂直线段与直角转折。圆形仅用于标准流程连接符，连接线不使用弧线。图中将恢复与终止规则集中列出，避免返工线横穿全部阶段。

## 实现依据

更新图示时先核对这些现役入口，不能只根据旧文档补画组件：

| 内容 | 实现 |
| --- | --- |
| 阶段顺序、人工信号等待、自动制卡开关、删除任务 | [workflows.py](../../services/research-platform/src/hrs_platform/jobs/workflows.py) |
| OCR 检查点、模型核验与阶段产物 | [activities.py](../../services/research-platform/src/hrs_platform/jobs/conversion.py) |
| 工作流注册、队列、outbox 派发 | [worker.py](../../services/research-platform/src/hrs_platform/jobs/worker.py) |
| 查询嵌入、重排与索引代际 | [search.py](../../services/research-platform/src/hrs_platform/services/search.py) |
| 容器、数据库与主机端点 | [compose.yml](../../deploy/platform/compose.yml)、[nginx.conf](../../deploy/platform/nginx.conf) |
| OCR 隔离进程、视觉服务与共享 GPU 准入 | [local_vision.py](../../scripts/local_vision.py) |

特别注意：API 事务提交 outbox，CPU worker 中的 dispatcher 再启动或唤醒 Temporal 工作流；OCR 在独立进程运行，broker 负责协调显存并提供视觉与检索模型服务。待审内容不能绕过整书入库门槛，未通过的卡片不能画成自动采用。

## 制图与检查

采用 [ASQ 流程图约定](https://asq.org/quality-resources/flowchart)中的顺序、判断和连接符表达，以及 [C4 图示规范](https://c4model.com/diagrams/notation)中的范围、职责、技术、边界和关系标注原则。架构图是组件与部署边界的综合视图，不声称是一张严格的 C4 容器图。

修改后检查 SVG XML、仓库内链接及连线方向，并在浏览器实际渲染：文字不能溢出节点，标签不能遮挡分支箭头，失败路径不能落入成功结果。缩略图用于总览，点击 SVG 查看全尺寸细节。
