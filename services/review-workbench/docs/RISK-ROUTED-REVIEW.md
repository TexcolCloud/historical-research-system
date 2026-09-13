# 文档核验风险分流（2026-09-10）

## 执行规则

先识别并导出完整 Markdown，再按物理页分流。`vision_review.review_mode=risk_based`、
`confidence_threshold=0.98` 为默认设置；`full` 恢复每页送审，不切换识别引擎或生产模型。

- 明确页级识别分数达到阈值、OCR/导出一致、版面框完整且无异常的简单内部页：规则放行。
- 置信度未知或低、首尾页、标题/注释/续页标记、表格/图、复杂布局、稀疏/乱码/重复文本：送 DeepSeek。
- 模型发现错误或未能完成核验：沿用局部拦截与审核卡；其余合格内容自动目录交接入库。
- 自动修订改变邻页上下文：受影响邻页转入最终核验，不能继续冒用规则放行。
- 人工确认无误：原有直接放行路径，不调用模型。显式修改后提交重核只对涉及页发起审核；
  其他页仅提供上下文并保留原决定，同页未提交块仍受 `preserve_unsubmitted` 保护。

当前 PaddleOCR-VL 的分数未知，因此仍送审；本轮没有生成假分数，也没有重新调优 OCR。
0.98 是可配置的保守起点，未作真实样本校准。高分不保证内容正确，规则也不能证明不存在漏识别。
结构风险页保留模型结构观察；跳过页明确记为 `not-model-reviewed`，不会生成虚假的篇目边界观察。
篇目划分仍由入库模块负责。

## 明确的接口与产物

- `review-routing.json`：审核前写出的调度计划、每页原因和证据摘要，仅供进度/排查，不能作为放行依据。
- `semantic-acceptance.json`：最终页记录含 `review_route`、`routing_decision`，后者绑定正文、原图及识别证据 SHA-256。
  `rule-pass` 页面保持 `verified=false`、无模型回执；`all_pages_accepted` 与 `all_pages_verified` 分开。
- `manifest.review_routing` / `ingestion_gate.review_routing`：总页数、本轮送核页数、规则通过数、未知置信度数、保留原决定数、模型审核任务数。
  这些计数存在交叉，不能相加当作总页数；审核任务可能命中已有模型缓存，不代表计费请求数。
- `content-readiness.blocks[].review_basis`：区分规则、模型或混合依据。人工放行仍显示独立人审记录。
- 新 `release_policy=risk-routed-errors-only-block-v1`：工作台使用同一份纯标准库规则重算并校验摘要后放行，
  不加载 OCR。旧产物仍按旧策略处理，不批量修改历史任务。
- 进度仍是阶段权重估算，计入规则通过/保留原决定页面；100% 仅代表转换完成，不表示人审通过。

## 验证与生效范围

### 版面置信度补充

转换适配器保留 Paddle `layout_det_res` 中真实的区域分数，并通过来源映射进入现有入库
`source_record`。新字段 `layout_evidence` 的含义如下：

- `confidence_kind=layout-detection`、`confidence_scope=region`，不写入 OCR 的 `confidence`。
- `frames[].detections[]` 保留 `label`、`bbox`、`confidence`；非法/缺失分数为 null。
- `minimum_detection_confidence` 仅是已返回区域分数的最小值，不是全页正确概率。
- `status=available|partial|unavailable` 描述证据完整性，不表示分数高低或审核通过。
- `source_image_sha256` 绑定提供给识别适配器的图像；坐标以 `provider-layout-frame-pixels` 标记，
  在预处理及多帧坐标映射未核验前，`original_image_coordinates_confirmed=false`。
- 检测器未明确启用时不使用可能由管线生成的 `score=1` 整页占位框；其他识别后端或历史产物无此字段时保持未知。

入库分件草稿的来源段证据增加 `upstream_layout_detection`，只附对应页证据，不在每个段中重复整篇版面。
DeepSeek 分件辅助请求增加 `layout_evidence`，提示其区分检测分数、文字准确性和篇目归属；
提示版本升级为 `partition-assistance-2-layout-evidence`，旧提示缓存不混用。
纯规则分件继续依赖原有边界信号，不因一个高分标题框自动建篇、放行或解除人审。
来源段只截取了部分文字时，版面证据仍为整页上下文，不伪造框到 Markdown 字符的精确对应。

工程验证包括模拟 Paddle 输出到实际产物写出、缺失/停用检测器回退、独立 PostgreSQL 入库保真、
分件草稿/辅助请求携带证据及高分版面不自动认定篇目。未作真实检测准确率评估，也未调用真实模型。
此补充需要重启入库 API/worker 后用于新任务；旧产物不自动回填，转换重试仍遵守现有代码指纹缓存校验。

### 风险分流工程验证

工程夹具覆盖跳过普通高分页、风险回退、全文模式、证据变更失效、改变邻页后的核验失败拦截、
修订范围保持、规则通过产物自动入库门槛、独立后端解释器无需 OCR 依赖、前端状态及进度说明。
使用合成数据与注入审核函数，不是对真实史料的准确率评估，不构成 GPT 或人工生产放行。
本轮不调用真实 OCR/DeepSeek/GPT、不修改现有任务数据库、不重跑用户 PDF。

代码与构建产物已更新；本轮没有重启长驻服务。转换子进程后续启动时加载新配置与代码，
但工作台转换适配服务必须一并重启才能识别新放行策略；应完成服务重启后再开启新转换任务。
