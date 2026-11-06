# Timed single-camera contract correction — 2026-11-06

按用户统一契约修正 ec51cd1 的两个未发布 pack，版本仍为 0.1.0。
本次只修改两个 cams-array pack、自有测试和报告；没有重启服务、push、发布
registry、修改 repeat-4d / 前端 / 源 job 或提交另一会话文件。

## 类型与时间

- 数组标签统一为 arrayed<colmap4d-single-cam>[camera]。
- 叶子恰好包含 model / width / height / params / qvec / tvec / timestamp_ns。
  时间直接位于元素 JSON，为 signed int64 ns，拒绝 bool / float / string / null / 越界。
- colmap-single-cam 仅含六个几何字段；两个数组 pack 不再使用 colmap-camera 标签。
  旧版本其它 pack 的标签不在本会话修改范围，交由负责会话处理。
- sidecar 仍保存身份、原始记录、顺序、完整叶子哈希和冗余时间快照，但 from-array
  从叶子的 timestamp_ns 写回 times.bin；sidecar 的时间值不再作为恢复来源。
- 逆转换仍要求未修改叶子和来源证据，不把它扩展为任意编辑/导入节点。
  内部 provenance 升为 v2；旧 v1 六字段数组需从原组件重新生成。
- interval-pick 保留完整七字段叶子。repeat-4d 0.3.0 可直接读取类型内部的时间，
  无需依赖 sidecar；具体消费/调度语义由另一个会话维护，此处未改动其文件。

实现：两个 pack convert.py 的 timestamp:103、leaf:110、to_array:124、from_array:162。

## 转换节点与并发分工

首次检查时 community_packs/single-cam-drop-time@0.1.0 已存在，处于另一会话
未跟踪文件中，故按规则跳过创建。已只读检查其 manifest / 实现并执行真实 CLI：
col4d/single-cam-drop-time@0.1.0，colmap4d-single-cam → colmap-single-cam。
剥离 timestamp_ns 后六字段逐值一致。未修改、暂存或提交该 pack。
verification.json 记录校验时 drop_time.py 的 SHA-256，明确这是已有文件的验证快照。
提交前复查发现另一会话已以 8104f1e 提交该转换节点及 repeat-4d 0.3.0。

## 版本与引用

用户确认两个 0.1.0 刚创建且未发布，因此原位修正，不增加 pack 版本。
本地 git branch -a --contains ec51cd1 仅显示 main；git tag --contains 无输出。
仓库搜索仅找到本次 pack 的测试与报告引用，没有发现其它已提交消费者。
旧报告保留为历史证据并标记已被替代，旧验证入口转向当前验证脚本。
提交前 main 已前进到另一会话提交 8104f1e，ec51cd1 是其祖先，已被本地后续
历史引用；本次追加修正提交，不重写 ec51cd1 或其它会话历史。
未联系远端或线上数据库，不能据此排除外部工作流或外部仓库已经引用 ec51cd1。

## 验证证据

- tests/test_colmap4d_cams_array.py：17 passed。覆盖七字段、int64 边界、完整往返、
  真实 interval-pick、子集身份、共享内参、重复选择、损坏/混源拒绝、整数组迁移。
  新测试刻意修改 sidecar 时间快照，证实 times.bin 恢复使用叶子时间。
- 两个修改后的 manifest 和已有 drop-time manifest 均通过 schema 校验。
- 真实来源：workflow 9967b1f8-3646-41f5-a269-394b6d392169，job
  03fb2084-8f8c-43c2-9bda-aa27955ede38/4d_cams；只读，81 张相机。
- 新产物：/cloud/cloud-ssd1/Kiri4DGS/tmp/cams-array-nyomr92x/
  （array、restored、picked、subset、timeless.json）。
- cameras.bin / images.bin / times.bin / time_meta.json 的来源与恢复 SHA-256
  全部一致。完整哈希、CLI 命令、产物路径及抽样 ID/时间见 verification.json。
- times.bin SHA-256：a58d8197fdf568ac61f4aef8be8ac4baa858f6d1ab6ed8c7c19b22d04ad1b59d。
- images.bin（1,007,776,898 字节）SHA-256：
  cfc3fd5904a45f02dc74c017f9d153298f8dc15c6831b1fd3144e4672b6eace7。
- interval=10 选出 9 张，叶子内部时间、恢复后的 ID 和 times.bin 全匹配。
- 未声明自动 array_view：当前 partition schema 仅支持 exposure_groups，
  per-image policy 需另行平台改造；类型之间没有 view-key 冲突。

## 待用户安排加载后验证（本任务没有重启）

- 新节点目录和端口显示 colmap4d-single-cam，不再缓存旧 colmap-camera 标签。
- split → cams-to-array → interval-pick → repeat-4d 0.3.0 的在线 prepare/fan-out，
  确认实际消费叶子内时间。
- timed leaf → single-cam-drop-time → 六字段消费者的在线连线与执行。
- array → cams-from-array 的在线往返、子集身份路径和产物预览。
- 配 MegaSAM 点云 → colmap4d-render-video 的端到端输出。
