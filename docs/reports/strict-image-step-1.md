# 严格 image 契约：第一步交付

范围：核心代码和当前主链 pack；历史版本、四份规范文档的批量修订、旧测试迁移留在第二步。没有重启服务，没有推送。

## 责任边界与实现

- 系统按结构化声明验证实际交付物；开发者负责声明符合算法要求，不分析算法代码。
- `image` 是一个可完整解码、单帧的图片文件。声明必须 `storage: file`；图片集合使用 `arrayed: true`、`storage: dir` 和外层优先的 `dim_labels`。不引入字符串类型语法。
- 拒绝 `image_sequence`、`frame_sequence` 类型 tag，包括与 `any` 混用的情况。名为 `frame_sequence` 的端口没有改名。
- 每个声明描述 **一次算法调用**。节点 fan-out 在映射输入和输出外面增加 **一层** 集合，分片只拆掉这一层。`scalar: true` 输入广播整份声明值，不能绕过数组/单图校验。
- 图片没有目录内计数这一“内在维度”。测量器扫描显式集合层；文件叶是 image，维度证据是完整集合扫描；COLMAP 内部 pose/intr 等计数仍是各自的观测。
- 校验时机：声明加载、前后端连线兼容、计算节点执行前、整个 batch 成功后且注册任何输出前、缓存输出重新发布前。数组逐叶验证，不使用摘要抽样替代契约校验。点开头的框架元数据不算元素；符号链接的目标也要通过验证。
- 对非 image 的不透明 bundle，本次验证显式数组容器和路径存在，不新增每种格式的内容解析器；`any` 是明确的泛型，不猜测算法意图。
- 单图直接预览文件。图片数组使用序列预览；不再用旧 tag 选择预览器。

关键实现：

| 文件 | 内容 |
|---|---|
| `hololab/manifest/data_contract.py`、`schema.py` | 声明约束与逐叶解码；新增 Pillow 运行依赖 |
| `hololab/gateway/port_type_ir.py`、`workflows.py` | 严格兼容、单次调用与 fan-out 分层、泛型维度传播 |
| `hololab/gateway/execution.py` | 一次只拆外层；文件元素枚举；普通/批量分片 storage=file；未连接可选输入不参与 zip |
| `hololab/node/runtime.py` | 执行前与发布前契约校验；实际文件注册为 file |
| `hololab/gateway/tag_probes.py`、`handle_summary.py` | 删除 image 内容维度，显式文件叶测量 |
| `hololab/gateway/app.py` | 区分父任务聚合产物与分片产物；以解析后的叶 tag 测量泛型产物 |
| `hololab/gateway/tag_viewers.py`、前端 `previews.tsx` | 单图片文件预览 |
| 前端 `tags.ts`、`edgeLabels.ts`、`App.tsx` | 与后端一致的层数/轴标签校验与泛型解析 |
| 前端 `typeTooltip.ts` | 普通节点保留调用全部维度；仅 fan-out 视角拆一层 |

## 已迁移 pack

| pack | 单次调用声明 |
|---|---|
| image-undistort 0.5.0 / 0.5.1 / 0.5.2 | images 输入/输出 `arrayed[cam]<image>`；cams 是 bundle，输入广播 |
| colmap-triangulate 0.7.2 / 0.7.3 / 0.7.4 | frames 输入 `arrayed[cam]<image>`；points 输出一个 bundle |
| colmap-sfm 0.3.0 / 0.4.0 | frames 输入 `arrayed[cam]<image>` |
| merge-colmap 0.4.0 | 可选 images 为 `arrayed[cam]<image>`；cams/points/folder 为单 bundle |
| frame-extraction 0.1.0 | 输出端口仍叫 frame_sequence；类型为 `arrayed[frame]<image>` |
| regroup-by-frame 0.1.0 / 0.1.1 | `[cam,frame]` → `[frame,cam]` 显式图片数组；不额外生成 frames/ 层 |
| regroup 0.2.0 | 文件或 bundle 叶的显式维度置换；不推断 image 内容维度 |
| get-index、array-length 0.1.0 / 0.1.1 | 枚举文件/目录元素以及符号链接 |

`arrayfy@0.1.0` 已通过符号链接支持文件，不需要改 pack；其类型传播在公共解析器中保留输入结构再包一层。`regroup.content_dims` 仍接收当前保存任务的 0/1 参数，但不改变类型或枚举深度；不再隐式展开旧 frames/ 布局。

## 验收与证据

反例在 `tests/test_strict_image_contract.py`：

| 反例 | 结果 |
|---|---|
| 图片数组连接到错声明单 image 的 und | `validate_snapshot` 拒绝边：target expects image |
| 绕过连线直接传目录 | NodeRuntime 在启动 executor 前失败：expected one image file |
| 真传单个有效 PNG 给 und | 真实 kiri Python 环境执行 und_worker，退出码 2：images handle is not a directory |
| 数组最后一个图片损坏 | 全量验证拒绝，证明不是只检查首个样本 |
| subprocess 成功退出但写出非法图片 | 运行时失败，不注册 handle、不发送 job_done |
| subprocess 写出正确单图 | 校验通过，注册 storage=file 并发送 job_done |

验证结果：

- 新契约与相关后端回归：148 项通过。
- 保留的类型 IR / 观测结构回归：33 项通过。旧兼容矩阵、旧内容维度等明确待迁移用例未计入通过数；**没有宣称全套后端测试已通过**。
- 前端单测：360 项通过。
- Playwright：6 项通过；输入/输出/文字/边中点、广播、普通节点、单图预览、浅深主题、50%/100%/150% 缩放、拖线、选边、几何不变。
- `npm run build` 成功，dist 已重打。Vite 仍有现有大 chunk 提示。部分旧数据库测试有 asyncio 清理警告。
- Ruff 检查及 git diff whitespace 检查通过。

真实算法 smoke：`tests/smoke_strict_image_chain.py`，独立临时目录，3 个合成相机、256×256 PNG、单帧、STG 1 次迭代（batch=2）。und / tri / merge / stg 均退出 0，阶段间运行数据契约均通过，生成 iteration_1/point_cloud.ply 和 point_cloud.pt。

这是独立进程的真实算法与数据交付验证，**不是在在线服务上提交的新 workflow**，也不是完整数据集训练效果验收。SFМ 相机先验由 fixture 构造；SFＭ 声明及主链连线另有集成测试。

运行：

```bash
HOLOLAB_ALGORITHM_PYTHON=/path/to/kiri/bin/python python -m pytest tests/test_strict_image_contract.py -q
HOLOLAB_ALGORITHM_PYTHON=/path/to/kiri/bin/python python tests/smoke_strict_image_chain.py
```

smoke 需要相邻 SpacetimeGaussians checkout 和现有算法环境/GPU。单元测试在没有该外部环境时会显式 skip 对应真实算法用例。

本次证据在 `/tmp/hololab-strict-image-evidence/`：`results.json`、四阶段日志、`light-strict-0.png`、`dark-strict-0.png`、两主题广播截图。截图已人工查看：左右栏区分调用图片集合与 fan-out 总集合，广播内容不变。

## 第二步待办（本次未实施）

17 个尚未迁移、现在会被新 image 声明规则拒绝加载的历史 pack：

- colmap-sfm-cams-only 0.1.0 / 0.2.0
- colmap-triangulate 0.1.0 / 0.2.0 / 0.3.0 / 0.4.0 / 0.5.0 / 0.6.0 / 0.7.0 / 0.7.1
- image-undistort 0.1.0 / 0.2.0 / 0.3.0 / 0.4.0 / 0.4.1
- merge-colmap 0.2.0 / 0.3.0

四份规范文档批量更新：pack-spec.md / writing-a-pack.md / workflow-schema.md / artifacts.md。

旧别名引用仍涉及 13 个既有后端测试文件（有些只引用同名端口或作历史说明，应逐条判断，不机械替换）：

- test_tags_from_propagation.py、test_port_type_ir.py、test_colmap_packs.py
- test_fanout_parallelism.py、test_new_type_system.py、test_ports_compatible.py
- test_handle_display_meta.py、test_edge_chip_arrayed_bundle.py、test_scheduler_arrayed.py
- test_arrayed_type_system.py、test_regroup_pack.py、test_agent_api.py、test_fanout_phase1_batch.py

另有 `test_type_observation.py` 中旧 image 内在维度断言，以及部分旧浏览器 fixture 的宽松类型/单层 OR 预期需要迁移（例如 arrayed-port-tooltip、type-ir-outlets 的旧 fixture；open-artifact-location、nested-frame-sequence-preview 的旧别名）。本次没有为兼容这些旧断言恢复宽松行为。

## 部署 / 需用户协调

- 用户负责外部消费者通知。API 原有 tags / dim_labels 等字段没有删除；类型规则变严格，消费者也需采用单次调用 + 外层 fan-out 语义。
- 用户安排 gateway 与 compute node 使用同一版代码/依赖再重启；本次没有重启，在线旧进程不代表新规则。Pillow 已加入 pyproject 依赖。
- STG 的 batch=1 smoke 曾触发其现有 `traincameralist` 未赋值错误；改回 pack 默认 batch=2 后通过。本次没有修改 STG。

## 后续状态

第二步用户改为删除不兼容历史版本；清单、完整测试及外部 pack 部署核实见 [第二步报告](strict-image-step-2.md)。本报告上文的“第二步待办”保留为第一步交付时的记录。
