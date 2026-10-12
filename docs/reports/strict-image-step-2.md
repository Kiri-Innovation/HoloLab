# 严格 image 第二步：删除不兼容历史 pack

用户改为删除旧版本，不再迁移。以下是本次分类、删除影响与验收记录。

## 工作树分类

- 保留：pack-spec.md、writing-a-pack.md、workflow-schema.md、artifacts.md 的严格 image 规范修订。
- 撤销：17 个历史 pack 的未提交迁移，包括 manifest、旧 frames/ 路径与相机键修补；恢复 HEAD 后仅删除这些目录。
- 保留：新契约、文件叶枚举、测量与现有 UI 的测试迁移。
- 清理：已删除版本的专属测试和未提交的 smoke_strict_image_history.py；仍有价值的共用算法行为在保留版本上验证。
- afab253 的核心与主链 pack 不撤销。无关 test-results/ 不纳入提交。
- FastGS-Pano 的 b162212 已提交，保留：heic-to-image@0.2.0 与 nerf-transforms-to-colmap@0.1.0 的图片集合声明。这两个 pack 被当前图使用，不属于此次删除名单。

## 加载失败如何处理

- hololab/manifest/schema.py:517 的 load_manifest 解析 YAML；:571–573 校验失败抛 ValueError。
- hololab/node/packs.py:57–64 的 _load_manifest_safely 捕获异常，记录 warning 并返回 None；scan_packs 跳过该 pack，节点不会因此整体退出。
- hololab/gateway/registry.py:480、:531 在已有 inventory 上重新解析 manifest，catalog 路径没有同样的捕获，异常可能导致该 API 请求失败。因此不能把所有路径概括成“只警告”。目录删除后查不到 manifest 会返回 None；库存刷新前可能短暂显示空签名。

## 删除清单与风险

只读查询在线 workflows.draft_json / snapshots.graph_json，2026-10-12。快照数是引用该版本的快照数量。全部待删版本的 pending/assigned/running job 数均为 0。

| Pack | 当前草稿引用 | 快照数 |
|---|---|---:|
| colmap-sfm-cams-only@0.1.0 | cc-e2e-arrayed-cook3cam / sfm (3d5b2139) | 8 |
| colmap-sfm-cams-only@0.2.0 | 无 | 0 |
| colmap-triangulate@0.1.0 | cc-e2e-arrayed-cook3cam / tri (3d5b2139) | 8 |
| colmap-triangulate@0.2.0 | 无 | 0 |
| colmap-triangulate@0.3.0 | 无 | 0 |
| colmap-triangulate@0.4.0 | 无 | 0 |
| colmap-triangulate@0.5.0 | 无 | 0 |
| colmap-triangulate@0.6.0 | 无 | 0 |
| colmap-triangulate@0.7.0 | 无 | 8 |
| colmap-triangulate@0.7.1 | 无 | 31 |
| image-undistort@0.1.0 | 无 | 0 |
| image-undistort@0.2.0 | 无 | 0 |
| image-undistort@0.3.0 | 无 | 0 |
| image-undistort@0.4.0 | FastGS-Pano pipeline (hololab-integrate) / undistort (f8d7afc3)；FastGS-Pano pipeline (hololab-integrate) / undistort_b (f8d7afc3) | 9 |
| image-undistort@0.4.1 | 无 | 16 |
| merge-colmap@0.2.0 | 无 | 0 |
| merge-colmap@0.3.0 | 无 | 0 |

3 个版本有当前草稿引用，3 个只在历史快照中，11 个无当前数据库引用。均按用户明确指令删除，不删除或自动升级工作流。

**直接影响**：旧 cc-e2e-arrayed-cook3cam 的 sfm/tri，以及 FastGS-Pano pipeline 的两个 undistort 节点将缺少指定版本；再次执行前必须显式选择保留版本并检查参数、端口契约。删除并不等于这些旧草稿已修复。

SnapshotCanvas.tsx:90、:269 从 catalog 查 pack；缺失时使用空端口 stub，不会白屏，但端口、预览与类型信息退化，关联连线也无法完整呈现。历史记录和已有产物不删除。Git 保留旧 pack 内容，可追溯；若恢复未经修正的旧版本，严格规则仍会拒绝。

## 保留与部署

当前主链 und 0.5.0/0.5.1/0.5.2、tri 0.7.2/0.7.3/0.7.4/0.7.5、sfm 0.3.0/0.4.0、merge 0.4.0、frame-extraction、regroup-by-frame、slice、video-array-source 和数组工具不删。

没有重启服务、没有 push、没有修改在线图或历史数据。部署需要 HoloLab 和 FastGS-Pano b162212 一起发布，由用户安排 gateway / compute node 同版重启与外部消费者协调。

## 删除后验证

全部结果均在删除后重新取得，没有沿用删除前的迁移验收。

| 验收 | 结果 |
|---|---|
| 仓库 pack 逐个 load_manifest | 29 / 29 通过；删除集合正好等于恢复迁移前新规则拒绝的 17 个版本 |
| 节点全部配置 pack_dirs | scan_packs 共 58 个；逐个直接 load_manifest 无失败（仓库 29、FastGS-Pano 8、rig calibration 16、user packs 2、STG 2、STG_to_SplaTV 1） |
| 后端全套 | 1097 passed，3 skipped，592 warnings；无失败 |
| NumPy 环境补跑 | test_incomplete_group_packs.py 在 kiri 环境 1 passed；补足上述一项 skip |
| 前端单测 | 16 个文件，360 passed |
| 浏览器全套 | 原有 61 项全部通过；另补浅/深主题缺 pack 快照 2 项通过，共 63 项 |
| 构建 | npm run build 成功，vendor 与 dist 已重打；保留现有大 chunk 警告 |
| 真实小规模主链 | und@0.5.2 → tri@0.7.4 → merge@0.4.0 → STG@0.3.0，四阶段 exit 0，所有交付契约检查通过 |

后端剩余跳过：需显式提供在线 workflow ID 的画布检查；依赖一处已不存在的历史 fx 产物路径的 regroup 对照。浏览器 fixture 已覆盖拖线、边选择、缩放、tooltip 和快照，但没有用它们冒充在线旧草稿已经可以运行。警告主要来自既有 asyncio/Starlette 测试生命周期。

真实算法验证使用 `tests/smoke_strict_image_chain.py`，3 个合成相机、256×256 PNG、单帧、STG 一次迭代；独立进程，不提交在线 job，不重启服务。产物与日志在 `/tmp/hololab-strict-chain-qslbi7an/`；这是小规模契约/执行验证，不是完整数据集质量评测。先前的三个错误声明反例仍包含在通过的 `test_strict_image_contract.py` 中。

### 测试清理范围

- 删除 `test_sfm_cams_only_stg_parity.py`，清理 test_colmap_packs / test_new_type_system / test_typed_camera_packs 内只针对已删版本的测试。
- 保留 SfM 相机键解析、merge 文件名改写等有效回归，分别转到 tri@0.7.4、merge@0.4.0；und 的调用轴/父集合轴测试转到 @0.5.2。
- 不修改算法来迁就旧测试：旧别名、隐式目录图片计数、广播吞掉数组层次等预期改为严格规则。
- 更新旧状态 UI 断言到已上线的单状态点；补齐测试数据库关闭，避免全套测试泄漏线程。
- 新增 retired-pack-snapshot.spec.ts：模拟 catalog 缺失 pack，断言节点仍可读/可选中、端口为空、没有页面异常。截图中的 merge@0.4.0 是测试 fixture 模拟缺失，不表示实际删除了该版本。

### 截图与日志

已查看浅/深主题截图。当前严格图片契约与两栏 tooltip 正常；缺 pack 快照仍显示节点身份与状态，端口及其连线退化：

- [浅色严格契约](/tmp/hololab-strict-retirement-evidence/light-strict-0.png)
- [深色严格契约](/tmp/hololab-strict-retirement-evidence/dark-strict-0.png)
- [浅色缺 pack 快照](/tmp/hololab-strict-retirement-evidence/light-retired-pack-snapshot.png)
- [深色缺 pack 快照](/tmp/hololab-strict-retirement-evidence/dark-retired-pack-snapshot.png)

其余截图、测试日志、catalog 与引用核实 JSON 在 `/tmp/hololab-strict-retirement-evidence/`（本机临时证据，不进 git）。

### 遗留与部署边界

- 两个引用旧版本的草稿不会自动迁移；再次运行前需显式选择保留版本并核对契约。
- 旧快照保留运行记录及已有产物，接受上述缺 pack 展示退化；不恢复旧兼容规则。
- FastGS-Pano `b162212` 是另一仓库已提交的两个当前在用 pack 修正，部署时需一并发布；本次没有 push。
- 用户负责服务重启和外部消费者通知。本次没有额外生产代码改动。

本次保留/调整的测试文件清单：

- `hololab/frontend/e2e/arrayed-port-tooltip.spec.ts`
- `hololab/frontend/e2e/canvas-inflight-drift.spec.ts`
- `hololab/frontend/e2e/fixtures/type-ir-snapshot.tsx`
- `hololab/frontend/e2e/nested-frame-sequence-preview.spec.ts`
- `hololab/frontend/e2e/open-artifact-location.spec.ts`
- `hololab/frontend/e2e/type-ir-outlets.spec.ts`
- `tests/test_agent_api.py`
- `tests/test_arrayed_marker_contract.py`
- `tests/test_arrayed_type_system.py`
- `tests/test_colmap_packs.py`
- `tests/test_edge_chip_arrayed_bundle.py`
- `tests/test_fanout_parallelism.py`
- `tests/test_fanout_phase1_batch.py`
- `tests/test_handle_display_meta.py`
- `tests/test_handle_lookup_api.py`
- `tests/test_image_undistort_manifest_dim_labels.py`
- `tests/test_incomplete_group_packs.py`
- `tests/test_new_type_system.py`
- `tests/test_node_staleness_bundle.py`
- `tests/test_port_type_ir.py`
- `tests/test_ports_compatible.py`
- `tests/test_regroup_pack.py`
- `tests/test_resolve_latency.py`
- `tests/test_scheduler_arrayed.py`
- `tests/test_tags_from_propagation.py`
- `tests/test_type_observation.py`
- `tests/test_typed_camera_packs.py`
- `hololab/frontend/e2e/retired-pack-snapshot.spec.ts`（新增）
