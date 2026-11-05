# 目录、阅读和检索的统一定位

核对通过后，原书目录中的标题及页码整行可以点击，阅读器跳到对应正文并短暂高亮；返回时回到原目录条目。侧栏提供原书目录层级及印刷页码。未可靠关联的条目保留普通文字，并提供现有人工目录核对入口。

搜索正文及上下文使用同一定位；命中目录载体时，单一条目可直达正文，包含多个条目的旧索引片段分别列出对应链接，不默认选择第一条。用户可以明确选择“此目录及下级”检索，默认仍检索全书。

## 数据与调用职责

- `services/documents/reading_locations.py` 生成统一 `ReadingTarget`：章节、运行、物理页、canonical Markdown 的 Unicode 码点范围、文字及原图哈希。目录目标以已有页内位置和 `parts` 来源记录换算；不重新识别目录，不猜固定页码偏移。
- `Library.reading_navigation(book_id)` 和 `GET /api/v2/books/{book_id}/reading-navigation` 返回发布版本目录；章节返回 `text_sha256`、`toc_links` 和 canonical→reading 的 `reading_segments`。新版本核对期间仍使用旧发布快照，不混用实时目录。
- 原始 Markdown、来源、脚注、文章划分和发布对象不变。结构替换区间不能精确映射时提供原图回退；无法唯一定位的目录原文不生成内联链接。
- 前端 `reader-location.ts` 统一路由及范围换算，URL 决定当前章节和位置；`projectSourceRange` 由阅读与审核共同使用，完整渲染后核对显示文字，避免前缀重解析、重复标题和跨段误定位。定位依据失效时不标记其他文字。
- 目录源链接返回到对应源页和条目；搜索查询、选项与范围保存在 URL，阅读后可“返回检索结果”，浏览器前进/返回也保留查询状态。目录修正跳到已有单条人工核对页面，不在旧阅读快照中直接改写。

## 检索范围与兼容

`retrieval/directory_scope.py` 根据发布目录及文章顺序形成 canonical 区间，范围延伸至下一同级或上级目录的正文边界。边界使用实际 Markdown heading block 起点，不使用为了高亮而收窄的可见字坐标，避免把下一章的 `# ` 或前导空白纳入上一章。代码中的 `# ` 不按标题处理。起止尚未确认的范围禁用，后端同时校验。

范围约束贯穿词法、向量、分解查询及结果扩展；主命中和普通邻段严格限定在范围内。已有来源关系明确的脚注、归属说明及必要表头等可随正文保留，位于范围外时明确显示。候选被裁切时重新核验脚注引用关系、重建表格/段落补充，不继承被排除正文的无关上下文。

目录路径原本已经参与检索。本轮复用既有 `toc_path`、`toc_entry_id`、来源范围和向量缓存，不改变 CHUNK_RULE 或索引 generation。旧目录片段缺少单一条目 ID 时，以发布目录源范围解析多条正文链接。

## 验证结果

| 范围 | 结果 |
| --- | --- |
| 平台、runtime-support 与 local-vision 脚本 | 448 passed，15 skipped |
| 前端 | 39 个文件，130 passed |
| 静态与构建 | Ruff、TypeScript、check-unused、构建及 diff 检查通过 |
| 225页真实已审书籍 | 23篇文章；16条可靠目录的 API 来源范围及浏览器落点逐条通过，16处原文链接可点击；1条未关联目录没有强制跳转 |
| 真实范围检索 | 3个固定关键词 × 3个目录范围，共9组；主结果均位于所选范围内；另以实际 BGE 查询核验语义检索范围 |
| 目录载体命中 | 真实旧索引片段返回16条明确正文链接，没有默认跳向首条 |
| 真实脚注补充回归 | 保留的独立10页机器发布资料17条脚注，正文/注释往返通过；Markdown导出20,960字节，与改动前逐字节相同 |
| 独立新史料 | 首48页完整走真实 Terra/Sol 流程，42页通过、6页待人工；没有发布章节或代替人工放行 |
| 用户数据 | 225页原件/页面/任务/文章组织/发布哈希，以及9条核对记录、9条人工决定、23篇文章记录保持不变 |

真实新材料为《中国共产党抗战大后方历史》上册母本物理第1–48页；在查看该材料候选与原图前冻结范围、母本和子集哈希，并预注册下册备选，未启用备选。6个待审物理页为2、4、22、23、38、42。该样本用于验证真实处理及未通过时的阅读发布门槛；没有把未发布内容包装成完整阅读验收。

阅读功能在已完成审核的225页实际出版投影的隔离只读副本上验证，再部署到原工作台复核。原图优先抽查物理第7页后，核对目录首项的实际正文标题与焦点，记录为机器开发验证，不作为新的内容人工批准。

关键回归覆盖：同页多篇、重复标题、emoji、源码唯一性、旧发布快照、过期哈希、结构替换边界、已有链接整项回退、跨标题及段落高亮、历史返回同一位置、目录修正可达性，以及保留脚注的范围过滤。共用后端真实目标 JSON 检验 API→URL→阅读映射→DOM，避免前后端各自构造理想输入。

范围机制的集成测试使用真实 SQL/S3/OpenSearch，向量及重排采用确定性替身；真实书籍矩阵使用现有索引和实际 BGE 路径。验证证明定位与范围行为，不是语义相关性或历史内容准确率评分。

## 本地证据

`output/reading-navigation-20261106/` 保留：

- `platform-delivery.log`、`frontend-delivery.log`、`frontend-build.log`：最终测试和构建。
- `real-api-validation.json`、`browser-directory-proof.json`、`browser-inline-proof.json`：目录范围、16项落点及返回。
- `real-search-validation.json`、`browser-search-proof.json`、`deployed-search-proof.json`：真实查询、范围和阅读往返。
- `browser-stale-proof.json`、`browser-footnotes.json`、`export-regression.json`：回退、脚注与导出保持。
- `selection-frozen.json`、`real-result.json`、`independent-publication-gate.json`：新48页流程与未发布门槛。
- `protected-user.json`、`visual-verdict.json`、`deployed-proof.json`：原数据基线、机器视觉验证与部署结果。
- `scope-heading-red.log`、`scope-heading-green.log` 等红绿记录：实际发现的问题与关闭证据。

本地验证通过后以一个新 PR 提交本轮改进，原 PR #40 已合并，不再修改该 PR。
