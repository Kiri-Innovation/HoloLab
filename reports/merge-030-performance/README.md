# merge-colmap4d 0.3.0：大模型组装性能验收

## 结果

同一组真实 input handles，通过 A800 gateway POST `/api/jobs/run` 派发新包。旧 job 已完成，本会话没有停止、取消或重启它。**668.23 秒 → 12.16 秒，54.9 倍提速**。

| 项目 | 0.2.0 基线 | 0.3.0 最终版 |
| --- | --- | --- |
| job | 64f04741-f875-46ba-95af-1de4d5455cfd | 82776d4f-3d49-4ae1-bdb0-5ac5c3ce964f |
| gateway started → done | 668.2277 秒 | 12.1639 秒 |
| 算法进程内计时 | 旧版未报告 | 11.9962 秒 |
| 内存 | 主会话现场 RSS 42.4 GB，非峰值 | getrusage 峰值 RSS 4,454,704 KiB = 4.248 GiB |
| 状态 | done，strict_problems=0 | done，exhaustive-single-track |
| 点 / 图像 | 41,990,400 / 81 | 完全相同 |

初版新实现已达 12.71 秒 / 峰值 6.06 GiB（first-run/）。随后释放库批量读取前的 mmap 驻留页，减少并存 buffer，并补齐非规范 sidecar 排序的严格回退；最终复测如上。没有通过修改系统缓存、CPU 配置或库源码提速。文件缓存状态没有人为控制，因此这是实际工作负载对照，而非冷缓存基准。旧 job 已结束，无法追溯其峰值内存；不把用户提供的现场 RSS 冒充峰值，也没有为取得峰值重跑长任务。

现场估算“5500 万点”由真实 bin header 更正为 **41,990,400 点**。points3D.bin 2,477,433,608 bytes；points_t.bin 671,846,408 bytes；images.bin 1,007,776,898 bytes。全部点 track 长度为 1。input-sizes.json、baseline-job/log.json、benchmark-job/log.json、comparison.json 保存原始证据。

## 实现与根因

仅新增 `community_packs/merge-colmap4d@0.3.0/`；旧 0.2.0/0.1.5、其它 pack、colmap4d 库、SHARP 均未修改。

原版 table/binary_ids 先创建 ID set，再读取海量 Python Point3D/track/observation 对象；validate 又建全量 (pid,image,index) set，并逐对象校验、逐字段重新序列化。末尾 validate_full 虽已用 include_observations=False/include_tracks=False，仍建立点对象、ID set 和 sidecar 字典，因此它仍属重路径。

新 merge_colmap4d.py:122 接入 `colmap4d.arrays.read_point_arrays/read_image_headers`（库提交 170d0ef，前置 54d439f）。无库层改动。均匀 track 的原文件以 mmap 结构化数组读取，逐块 NumPy 运算；几何检查后及时释放 XYZ/RGB 数组。小型 cameras 保留原完整解析。没有调用会返回全量 Point3D dict 的读取器，也没有错误地以 include_tracks=False 的对象读取替代数组读取。

- :39 完整 sidecar 记录长度、数量、ID 覆盖校验；int64 时间直接读取，无 float 时间换算。
- :140 数组级完整 point ID/几何/误差校验；文件头记录数与去重后数组长度比对，防止库 last-wins 隐藏重复 ID。
- :177–187 对每个单观测点**全量双向关系 + 来源帧时间**验证。每个合法 observation 必须等于其 point 唯一的 (image,index)；因此合法 observation 总数=点数即证明无点漏配对。dense ID O(1) 向量映射，稀疏 ID 用 searchsorted。
- 多观测分支检查全部 track 图像/index 边界、全部 observation 引用 ID 和几何；双向关系、重复 track、质心时间仅做最多 4096 点及每图 4096 observation 的确定性抽样。没有把抽样宣称为完整认证。
- :99 对全部图像做像素文件双射、安全路径、完整解码、模式和分辨率检查；metadata 冲突和 device-camera 冲突报错。
- :235 二进制和图片直接复制，独立文件，无硬链接、无 ID 重排、无时间重新推算。

`strict_validate` **默认 false**，适合受控生产链组装；外来数据验收显式 true。严格模式保留完整旧校验和结尾 validate_full(strict)，位于新包 strict_legacy.py。仅增加阶段日志，算法不变。文本、非均匀 track、非规范排序等输入自动回退严格路径并说明原因；不承诺这些回退路径也有 55 倍提速。严格模式需按大内存场景配置资源。

默认快速模式没有调用最终库 validate_full；这正是模式区别，不能把 result 解读为 strict_problems=0。对本次单观测实际输入，关系/时间检查比“统计抽样”更强，是全量检查；对任意多观测输入仍有明确抽样边界。

## 等价性

最终 output-equivalence.json 对 **88 个模型内容文件、4,289,158,325 bytes** 逐文件记录旧/新 SHA-256，全部相等；包括五份 COLMAP/时间二进制表、time_meta.json、81 张图片和 .hololab-done。比较脚本 compare_outputs.py 可重跑。文件是独立 inode。

仅排除 gateway 生成的 `.hololab-metadata.json`：它包含不同 job/handle 的运行来源，本来就必须不同，不属于模型二进制。没有删除或改写它来伪造全目录相等。

## 快速校验有效性

test_fast.py、tests.log：5 个测试方法通过，其中 20 个独立破损 fixture 全部拒绝：重复/零 point ID、NaN XYZ、负误差、错来源帧时间、重复 time ID、截断 points/times、images 尾随字节、重复 image ID、错 track image/index、未知 observation point、缺 reciprocal observation、Inf observation、缺/多像素、错分辨率/模式、metadata 冲突。另有多 track 质心时间破损拒绝测试。

验证单 track 与多 track 小样 fast/strict 的全部模型文件哈希一致；在 fast 测试中把对象 read_points3D_bin/read_images_bin 替换成会立即失败的桩，确保快路径没有偷偷构造对象；strict 测试使用真实库、最终 validate_full，并保留文本和乱序 sidecar 回退测试。真实大数据 benchmark 不使用 mock。

Schema 验证通过（schema.log）。新旧大数据输入 handles 完全一致（comparison.json），原图像/时间/ID 无变化。

## 版本、部署与遗留

版本 **col4d/merge-colmap4d@0.3.0** 已被本机自动发现，未发布中央注册表、未 push、未重启服务。此次没有自动迁移用户 workflow；nmw3rj68z2 仍指向 0.2.0，切换为 0.3.0 后才会使用新的默认快速路径。旧版本可继续用于历史快照。

两次真实新 job 的输出和旧基线输出均保留供核验。磁盘从约 155 GB 剩余降至约 139 GB（91% 已用，包含同时发生的其它写入）；没有清理任何用户产物，不再增加大样副本。库工作区未改，只有它原本由用户更新的 submodule 指针差异。
