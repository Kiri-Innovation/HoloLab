# colmap4d public port rename

2026-11-05。仅改指定 pack 的公开契约、配套文档/测试与 workflow；未改库、平台、前端或其它 pack，未重启、未 push、未发布中央源。

## 改动与版本依据

| 包 | 改动 | 版本处理 |
| --- | --- | --- |
| k4d/megasam-track | 输出 model → colmap4d；run.py 消费 outputs.colmap4d | 0.7.0 原位修改，用户明确授权，中央源 404，保存快照没有引用此版本 |
| col4d/colmap4d-split | 输入 model → colmap4d；exec 使用 inputs.colmap4d | 0.1.0 原位修改，用户明确授权，中央源 404，无已保存快照引用 |
| col4d/merge-colmap4d | 新版输出 colmap4d，exec 与 idempotency marker 同步 | 新增 0.4.0；0.3.0 保留 model。0.3.0 已有真实 jobs 和保存快照引用，不能破坏历史重放 |
| col4d/frames-to-colmap4d | 输出 model → colmap4d；exec 与 marker 同步 | 0.1.0 原位修改，中央源 404，全量草稿和保存快照无此包引用 |

所有端口 tag 仍为 colmap4d。merge 0.4.0 的 merge_colmap4d.py / strict_legacy.py 与 0.3.0 字节一致，只有公开端口和版本变化。MegaSAM 继续调用原固定版 merge 0.3.0 的内部 assemble 函数；内部函数参数不属于公开端口，因此不改 vendor 哈希或另写算法。

中央源实时查询见 registry.json：merge 目录 HTTP 200，但仅列出 0.1.5，未列出 0.3.0。这里区分“本机已有版本及历史引用”和“中央源已发布”，不把本机安装冒充中央发布。仍依据实际历史引用和用户所述已发版本保留 0.3.0。没有修改或重新发布旧版。

## 命名与引用调查

组件标准 docs/colmap4d-components.md 已明确 snake_case：4d_cams、4d_points、4d_images。它们保持不变；完整模型是单个单词 colmap4d，不添加连字符或下划线。类型 tag 与变量名是不同层次，这次只统一公开变量名。

repository-references.txt 保存全仓 rg 的 195 个引用文件路径，涵盖 manifest、测试、文档、历史验证 JSON/脚本及截图相关记录。旧报告/job 文件保留当时的 model，作为历史证据；它们不是当前生产 workflow，不能批量替换后冒充原始证据。内部目录变量 model 与 split CLI 的 --model 也不是公开端口，保持原有实现。

在线 API 查询了全部 3 个生产草稿及 26 个历史快照条目。22 个快照可读取，4 个历史条目返回 404，均明确记录在 snapshot-references.json；额外只读查询本机 gateway SQLite 的全部存储快照，结果在 snapshot-db-references.json，确认 frames/split/MegaSAM 0.7.0 没有存储快照引用。未修改数据库。

## 迁移

只有 9967b1f8-3646-41f5-a269-394b6d392169 的 nmw38vr8y4 使用此次原位修改包。使用最新 updated_ts 的 CAS 保存，删除过时的保存端口描述，gateway 重算为唯一输出 colmap4d。参数、输入节点及两条现有边保持不变。

该草稿上一轮已经移除 merge，MegaSAM 后没有消费者，因此没有旧 model 出边需要改写。其它两个草稿没有这四个相关版本的节点，不作无关修改。迁移脚本包含 producer sourceHandle、split targetHandle 和输入绑定的同步规则，现场迁移前后边记录见 migration.json。

回读 workflows-after.json 证明 MegaSAM outputs 只有 colmap4d；workflow-prepare.json：HTTP 200、run_blockers=[]。历史快照及旧 merge 端口保留兼容。

## 未连线输出的结论与方案

这是平台通用输出契约的限制，结合 split 当前的复制实现，不是 split 忽略了一个已有的按需输出接口：

- hololab/manifest/render.py:245 为 manifest 所有 outputs 分配路径，不传消费者集合。
- hololab/manifest/data_contract.py:118 的 validate_ports 遍历所有声明，缺少任何 output 都失败；仅 input 允许 optional 缺省。
- hololab/node/runtime.py:2776 的 _register_output_handles 校验并注册所有 output，并扫描目录大小。是否有图上消费者不改变这些行为。
- 实际独立派发 split（没有图上消费者）仍成功产出三个 handles，见 split-job.json。

**本轮不改平台**。split 无法通过当前 job 上下文知道用户只连了哪个口，也不能安全省略其它声明输出。临时查询 gateway 草稿来猜测消费者会受历史快照、ad-hoc 调用、后续连线变化影响；空目录不是合法组件。软链接会绑定输入生命周期；硬链接会共享可写 inode，破坏产物隔离，所以不采用。

平台改进建议（未实施）：在调度/缓存身份中传递 requested_outputs，并允许运行结果只注册请求的输出；规定后续新增消费者如何补算缺失端口，以及如何保留来源 handle。届时 split 可按该集合仅复制相应组件。更进一步可设计只读文件引用/虚拟目录产物，由平台管理引用计数和 GC。现阶段 pack 可评估 reflink 降低复制 I/O，但它仍生成全部输出、依赖文件系统支持，不能实现“未连线零开销”，本次不引入这种与命名无关的优化。

## 验证

schema.log：四个新版 manifest 全通过；test_colmap4d_port_names.py 的两个测试覆盖精确端口/tag、真实 Jinja exec/marker 渲染、旧 merge 保留和算法字节一致。

额外回归：frames XMP 2 个、merge 5 个、split 3 个测试方法通过，日志均保留。没有重新运行 4199 万点大样，因为几何算法未变；上一轮哈希证据保留。

本次小规模真实链使用原帧输入，skip=5/max_frames=16/pts_stride=8，16 帧 / 518,400 点：

| 阶段 | job | started → done |
| --- | --- | ---: |
| MegaSAM 0.7.0 | 90a49dd7-ce7c-4516-b406-11e32818e493 | 147.320 s |
| split 0.1.0 | 见 split-job.json | 0.400 s |
| merge 0.4.0 | 见 merge-job.json | 1.091 s |

megasam-job.json 只注册 colmap4d；split-request.json 只通过 colmap4d 输入，split 注册三个原组件名；merge-job.json 只注册 colmap4d。smoke-equivalence.json 记录 23 个内容文件 SHA-256 全等，仅排除每 job 的 .hololab-metadata.json。计时是共享 A800 的实际负载，不是独占性能基准。生产草稿没有留下 smoke 参数。

## 截图

- [四个包的浅色端口](ports-light.png)
- [四个包的深色端口](ports-dark.png)
- [目标 workflow 浅色](workflow-light.png)
- [目标 workflow 深色](workflow-dark.png)

四包截图来自真实临时 verification workflow，展示 MegaSAM → split → merge 的新端点，以及 frames 输出。未连接的数据输入如实显示未就绪，未伪装为运行成功。实际端到端成功证据是上述真实 jobs。截图后临时 workflow 已删除，截图图结构与删除结果保存在 screenshot-graph.json / screenshot-cleanup.json；未污染生产图。
