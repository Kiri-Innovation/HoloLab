# col4d 工具包身份修正（2026-11-05）

## 核实与决定

“另一个”与 XMP 照片转换包吻合：它接收已有 grouped points/cams 几何和带 XMP 的 flat JPEG image array，产出完整 colmap4d；不是仅凭图片做重建。采用 `col4d/frames-to-colmap4d@0.1.0`，README 显式保留几何输入要求。

中央官方源为 `https://packs.xenotech.studio/`（hololab/pack_registry.py:17）。直接请求官方 catalog 与每个包索引，完整响应见 central-catalog.json / central-proof.json：

| 名称 | HTTP | 版本 |
| --- | --- | --- |
| k4d/merge-colmap4d | 404 | 未发布 |
| k4d/colmap4d-from-xmp-photos | 404 | 未发布 |
| col4d/merge-colmap4d | 200 | 仅 0.1.5，目标 0.2.0 未占用 |
| col4d/frames-to-colmap4d | 404 | 未发布 |
| col4d/colmap4d-temporal-grouping | 200 | 0.1.2 |

因此两个本地未发布包原位改身份，版本号不变，没有发布或重发。`col4d` 已有中央正式发布先例；代码 validate_name（hololab/package_identity.py:16–31）只约束 scope/name 语法，没有 builtin/community/kiri/k4d 的封闭词表。按本次裁定，在组件 profile 文档明确 col4d 代表 colmap4d 格式工具生态；MegaSAM 算法仍属于 k4d。此次只连接和迁移 A800 gateway，未声称检查不可访问的其它实例。

## 文件与引用

- community_packs/merge-colmap4d@0.2.0/manifest.yaml:3、README.md：k4d → col4d；同步 XMP 包新名称。组装算法未改。
- community_packs/colmap4d-from-xmp-photos@0.1.0/ → community_packs/frames-to-colmap4d@0.1.0/。manifest name、README、merge_colmap4d.py:300 的产物 provenance source 同步为新身份，其余算法逐字与冻结旧版保持一致（兼容性测试）。
- docs/colmap4d-components.md:141–145 更新工具身份和 scope 定位。
- tests/test_megasam_ports_040.py、tests/test_colmap4d_xmp_010.py 同步路径和身份断言。

scope 来自 manifest name，不由扁平归档目录决定（node/packs.py 扫描 manifest）。因此保留 merge 目录，XMP 目录仅改 basename。未改 frozen col4d/merge-colmap4d@0.1.5；未改 MegaSAM 0.6.1、SHARP 或其它 pack。

三份在线 workflow 草稿全量检查：仅 single_video_4d（9967b1f8-3646-41f5-a269-394b6d392169）的 nmw3rj68z2 使用旧 k4d/merge-colmap4d@0.2.0，现已迁移。旧版 nmw3rirdp1 仍为 col4d/merge-colmap4d@0.1.5。没有 XMP 包的草稿引用。关联 recipe_dependencies 同步，参数不变，连线 ID/节点端点/端口不变。gateway 自动重算显示标签为 col4d/merge-colmap4d#2，不是改连线；migration-check.json 记录核对。

全仓文本搜索见 repository-references.txt；未改历史报告和快照。发现 MegaSAM 0.6.1 的 README 仍写旧 merge 名称；按“megasam-track 不动 / 别动其它 pack”约束保留，避免改变已验证算法包代码身份。当前 profile 文档提供旧→新映射；这是文档遗留而非活跃 workflow 引用。

## 验证

两个 schema 校验通过（schema-merge.log、schema-frames.log）；组件组装/XMP parity 6 项测试通过，接口/身份 3 项通过。测试分别使用原有算法环境及 runtime + 本地 colmap4d 源码 PYTHONPATH，不安装新依赖。

自动重扫后 /api/packs 出现 col4d/merge-colmap4d@0.2.0、col4d/frames-to-colmap4d@0.1.0，旧两个 k4d 身份消失，0.1.5 保持原 manifest_hash（packs-before/after.json）。首次读处在 watcher 延迟内，稍后重读已生效；未重启。

包管理器真实浅/深截图 col4d-light.png / col4d-dark.png 显示 col4d 分组和新名称，未 mock 页面。在线小规模链结果补于下节。

## 在线真派发完成

| 阶段 | job id | 时长（秒） |
| --- | --- | --- |
| k4d/megasam-track@0.6.1 | d016086a-5c3a-4c6f-8250-ad9268a278e8 | 105.59 |
| col4d/merge-colmap4d@0.2.0 | 2d592ab0-f005-4a66-8bb0-741edb5d1abe | 8.40 |

16 帧、518400 点，merge 日志 strict_problems=0，实际调用 validate_full 并按 strict 判定。MegaSAM 通过目标 workflow 的 prepare/dispatch 真派发；merge 通过 POST /api/jobs/run，以前一任务三个 output_handles 为输入。不是本地 CLI。时长为 updated_ts-started_ts；完整 request/job/log/handle 和快照 id 见 online/。

只临时设 skip=5/max_frames=16/pts_stride=8，frames_fps 采用既有有效值/默认；派发后草稿恢复，剔除系统 latest_run 后整图与冒烟前完全一致。没有修改 MegaSAM 包或源码。当前剩余文档事项仅为上述冻结 MegaSAM README 的旧名引用；无运行阻挡。未 push、未发布、未重启。
