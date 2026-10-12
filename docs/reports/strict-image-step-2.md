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

SnapshotCanvas.tsx:90、:269 从 catalog 查 pack；缺失时使用空端口 stub，不会白屏，但端口、预览与类型信息退化。历史记录和已有产物不删除。Git 保留旧 pack 内容，可追溯；若恢复未经修正的旧版本，严格规则仍会拒绝。

## 保留与部署

当前主链 und 0.5.0/0.5.1/0.5.2、tri 0.7.2/0.7.3/0.7.4/0.7.5、sfm 0.3.0/0.4.0、merge 0.4.0、frame-extraction、regroup-by-frame、slice、video-array-source 和数组工具不删。

没有重启服务、没有 push、没有修改在线图或历史数据。部署需要 HoloLab 和 FastGS-Pano b162212 一起发布，由用户安排 gateway / compute node 同版重启与外部消费者协调。

## 删除后验证

待完成后记录。之前迁移方案的测试结果不作为删除后的验收结果。
