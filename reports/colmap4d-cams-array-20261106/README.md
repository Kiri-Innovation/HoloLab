# Camera component array converters — 2026-11-06

> 历史报告：描述 ec51cd1 的旧六字段实现。统一契约修正及最新证据见
> ../colmap4d-cams-timed-contract-20261106/README.md；以下结果仅作为历史记录。

新增 col4d/colmap4d-cams-to-array@0.1.0 和
col4d/colmap4d-cams-from-array@0.1.0，沿用 colmap4d-split/to-array 的 scope。
仅新增两个 pack、独立测试和本报告。没有重启任何服务，没有发布 registry、
push、改前端、改其它 pack 或修改源 job。署名沿用本机 git config。

实现入口：to-array pack convert.py:115；from-array pack convert.py:153。
两个 manifest 的端口分别为 4d_cams ⇄ cameras，类型
colmap4d-cams ⇄ arrayed<colmap-camera>[camera]；每张 IMAGE_ID 一个元素。
两份独立可打包脚本一致，由测试保障。

选择 (c)：六字段 timeless JSON + 隐藏 provenance，而不是加可选时间字段。
repeat-4d@0.1.0 的严格解析不允许额外字段，修改它又不属于本任务。
隐藏元数据保存原 ID / int64 ns / 表记录顺序 / time_meta 原字节，隐藏二进制
归档保留 image NAME 和 observations。数组按 (timestamp_ns, IMAGE_ID) 排序。
interval-pick 仅按索引选择，链接仍可找到原 provenance；repeat-4d 只接收
原始位姿和内参，按自身参数生成合成时间轴，不继承原时间戳。
from-array 按原 IMAGE_ID 去重并恢复所选记录，保留所有原 CAMERA_ID 内参。

限制：二进制组件、非空选择；逆转换要求本转换器的未修改叶与 provenance，
拒绝跨来源混合，不为任意无 ID / 时间的 JSON 猜测身份。迁移数组时必须复制
隐藏 JSON 和隐藏归档；只复制六字段叶不具备可逆性。interval-pick 输出依赖
原数组存续。原始 to-array 输出和恢复的组件不依赖 job 源目录。

没有声明可选 array_view：hololab/manifest/array_views.py 的 Partition.policy
仅接受 source_exposure_groups，不支持 per-image policy；本任务不扩展平台。
colmap4d-cams 与 colmap4d 分属不同类型，机制按 (type, view) 查重，不存在
exposure_groups 名称冲突。目前请显式连接两个节点。

验证：

- 两个 manifest 通过 Manifest.model_validate schema 校验。
- tests/test_colmap4d_cams_array.py：10 passed（tests.log）；覆盖共享/未使用内参、
  乱序表、同时间不同身份、int64 边界、observations 保留、真实 interval-pick、
  repeat-4d 兼容、重复身份、混源/修改/损坏/缺失元数据拒绝、整数组迁移。
- verify.py 使用真实 CLI，在源 job 目录之外写产物；verification.json 含全部
  命令、源路径、产物路径、四文件完整 SHA-256 以及所选 ID / timestamp_ns。
- 源 workflow 9967b1f8-3646-41f5-a269-394b6d392169，job
  03fb2084-8f8c-43c2-9bda-aa27955ede38/4d_cams：81 张相机。
- 产物：/cloud/cloud-ssd1/Kiri4DGS/tmp/cams-array-d2hqyi5a/
  （array、restored、picked、subset、repeated）。完整往返四文件逐字节一致。
  images.bin 为 1,007,776,898 字节，SHA-256
  cfc3fd5904a45f02dc74c017f9d153298f8dc15c6831b1fd3144e4672b6eace7。
- interval=10 得到 9 张，原 ID 与原时间全匹配；真实叶经 repeat-4d CLI
  生成 2 帧固定相机（duration=1, fps=2）。仅验证已有标量消费，未声明线上 fan-out 已验证。

待用户安排加载新代码后验证（本任务不重启）：

- 节点目录发现两个新 pack，端口标签与 camera 维度显示正确。
- 实际图 prepare：split.4d_cams → cams-to-array → interval-pick，类型匹配。
- interval-pick → 另会话 repeat-4d fan-out：数组维度、调度和 synthetic 时间语义。
- array → cams-from-array 在线执行、子集 provenance 路径和产物预览。
- 配 MegaSAM 点云 → colmap4d-render-video 的端到端结果；未在运行中的图发起验证。
- 自动 array_view 需后续平台增加 per-image policy 再声明 provider，本提交不具备自动转换。
