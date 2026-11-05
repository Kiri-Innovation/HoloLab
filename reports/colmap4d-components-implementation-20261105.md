# colmap4d 三组件实施记录（2026-11-05）

## 旧草稿清理

按用户本次明确授权，删除未跟踪的 `community_packs/megasam-track@0.3.1/`，共五个文件：
`manifest.yaml`（旧 camera_track 接口）、`run.py`（启动器）、`guard.py`（参数路径守卫）、
`native.py`（旧 ml-sharp 适配器副本）、`README.md`（草稿说明）。没有删除任何已提交版本。

删除前核查：git ls-files 无该目录；tests/docs/hololab 源码及 hololab/reports 没有该完整身份引用；
只读 SQLite 检查 workflows、snapshots、workflow_recipe_intents、job_code_identities 中包含
megasam-track + 0.3.1 的候选记录均为 0，jobs 的算法名/版本组合为 0。
父仓库两份先前调查报告有历史草稿记录，作为事实历史保留不改。
草稿没有发布；本次不调用 registry 发布/删除，不改 catalog，不重启网关。

草稿原为 untracked，因此这一步的 commit 记录删除事实，而不存在 git tracked deletion。


## 实施阶段和提交

所有算法代码提交位于 `hololab` 仓库；未 push、未发布 registry/catalog，也没有重启 8828。

| 阶段 | commit | 改动与证据 |
|---|---|---|
| 应用层 profile | `98bdfad` | 新增 `docs/colmap4d-components.md`，定义 cams/points/images；v1.0 规范不改 |
| MegaSAM 新契约 | `31eead7` | 新增 `community_packs/megasam-track@0.4.0/{run.py,manifest.yaml,README.md}`、`tests/test_megasam_components_040.py` |
| 组件组装 | `80946ac` | 新增 `community_packs/merge-colmap4d@0.2.0/{merge_colmap4d.py,manifest.yaml,README.md}`、`tests/test_colmap4d_components_020.py` |
| XMP 独立 pack | `2328898` | 新增 `community_packs/colmap4d-from-xmp-photos@0.1.0/{merge_colmap4d.py,manifest.yaml,README.md}`、`tests/test_colmap4d_xmp_010.py` |
| 草稿清理 | `758c768` | 删除上节五个 untracked 草稿文件；新增本报告记录事实 |
| 真实样本适配修正 | `06f0e4e` | 仅修改本次新增 MegaSAM run.py/README/测试：支持原生空 POINTS2D 文本行；显式 SDR→sRGB；实测视频抽帧和原生调用隔离 |
| 验证报告 | 本报告最终提交 | 完整证据、限制及机器可读 `reports/colmap4d-components-smoke-20261105.json` |

### 组件 profile

| 组件 | 布局 | 不变量 |
|---|---|---|
| colmap4d-cams | 根目录 cameras.bin/images.bin/times.bin/time_meta.json | IMAGE_ID 关联、times 全覆盖；标准表不加 t 列；w2c 位姿 |
| colmap4d-point-cloud | 根目录 points3D.bin/points_t.bin/time_meta.json | 全视频唯一点 ID、points_t 全覆盖；MegaSAM 每点时间等于来源帧曝光中心；实际单源观测使用 track_centroid |
| colmap4d-images | images/NAME | NAME 与 cams 双射；尺寸等于 camera；单帧 RGB/RGBA 8-bit、SDR sRGB，RGBA straight alpha；不重复保存时间 |

三个端口均 `storage: dir, arrayed: false`，没有 dim_labels。
应用层 profile 不改变 colmap4d v1.0 的可选 sidecar 语义，只约束本组组件必须完整。
组件不能当完整模型；组装之后才能 validate_full。图片携带本身的像素，不依赖 scratch 软链接。

### megasam-track@0.4.0

输入 `video: video-source`（单文件或恰好包含一个视频的目录）；输出
`cams: colmap4d-cams`、`points: colmap4d-point-cloud`、`images: colmap4d-images`。
首次输出固定 binary。点云来自深度 Z 反投影，用 COLMAP 位姿统一世界坐标，并写实际像素观测与 reciprocal track。
逐帧来源时间保留 int64 ns；ERROR=0 表示反投影/重投影几何误差，不是置信度。

| 参数 | 类型 / 默认 | 校验 / 作用 |
|---|---|---|
| megasam_repo | PathInput directory，必填 | must_exist；复制 checkout 到私有 scratch，保护原算法输出目录 |
| tracker_python | PathInput file，必填 | must_exist；独立 MegaSAM 解释器 |
| sharp_repo | PathInput directory，必填 | must_exist；含 per-frame/video_to_colmap.py，仅调用 tracking wrapper |
| skip | int / 1 | ≥1，保留原 decoded_index |
| max_frames | int / 0 | ≥0，0 不限；在 skip 后限帧 |
| depth_min | float / 0.001 | 有限、≥0；只保留 finite depth > threshold |
| pts_stride | int / 2 | ≥1，二维像素采样步幅 |
| timestamp_mode | enum / exposure_metadata | values: exposure_metadata, pts_estimate, synthetic_uniform |
| exposure_metadata_path | optional PathInput file | exposure_metadata 模式必填；视频 SHA256 匹配、所选帧全部覆盖 |
| fps_num / fps_den | optional int | 均 ≥1；synthetic_uniform 时必填，不猜测 FPS |

默认 exposure_metadata 使用 profile JSON：video_sha256、clock_domain、frames；每条 decoded_index
对应 mid_exposure_ns，或 exposure_start_ns + exposure_ns//2。去重并检查 int64 边界。
PTS 模式使用真实整型 PTS×有理 time_base；均匀模式使用原 decoded_index 与指定有理 FPS；均标 estimated。

调用原 DA/UniDepth/MegaSAM/RAFT/CVD 链，不调用 SHARP inference，不提供 Gaussian/优化开关。
NPZ、缓存、原生 pts 等保留在 scratch，均不是公开输出。子进程去除跨环境 PYTHONPATH/PYTHONHOME/
LD_LIBRARY_PATH 并禁止写 bytecode。原生算法源码不修改。

### col4d/merge-colmap4d@0.2.0

输入 points/cams/images 三个 scalar 组件，输出 model: colmap4d。
检查标准记录重复 ID/二进制完整性、时间全覆盖、每图相机存在、位姿/几何有限性、
图片安全相对路径/双射/完整解码/尺寸、双向 track、单源逐点时间、track_centroid 多源整数均值。
两份 time_meta 递归合并：重叠值冲突报错，保留未知可选字段；spec/unit/convention/clock_domain 必需。

为了正确处理合法的空 POINTS2D 文本行，组件读取器不依赖会丢弃空行的旧 fallback 文本读法。
组装统一写 binary 到 sparse/0，语义上的所有 ID、坐标、观察、ns 均不重映射；不是逐字节原样复制文本。
像素 copy 到 model/images；不读取 XMP、不从文件名重推时间。最后调用 colmap4d.validate_full，
以 exit_code(problems, strict=True) 判断成功，之后才写完成标记。

### col4d/colmap4d-from-xmp-photos@0.1.0

新名字已采用。输入保持旧 `points: array[group]<point-cloud>`、
`cams: array[group]<colmap-cams>`、`images: flat array<image>`，输出完整 `model: colmap4d`。
保留 gXXXXXX__camN.jpg + 标准 HoloLab XMP、wallClockEpochNs + exposureNs//2、相同 timebase、
track centroid、ID 分配等旧行为。脚本内容测试证明唯一变化为 conversion.source 新身份。
旧版空 image observations + 保留 triangulation track 的兼容行为也保留，**不声称满足新组件双向 track profile**。
合成 XMP 场景新旧组装输出标准 binary/时间 sidecar 字节一致；time_meta 除 source 外完全一致。

## 验证证据

1. 三个新 manifest 均由本机 `hololab.manifest.load_manifest` 成功加载。
2. 真实运行环境 `/cloud/cloud-ssd1/envs/kiri/bin/python`：**9 项 unittest 全部通过，0.320 秒**，含多个子案例。
   - MegaSAM：有理时间/负 PTS/缺失曝光/哈希错误/重复索引/非法 bool；点坐标、ID、时间、双向 track；
     binary 与原生空观测文本等价；真实 ffmpeg 4 帧视频 skip=2/cap=2、曝光关联、sRGB 转换。
   - 视频入口测试使用明确的 CPU tracker fixture，验证调用参数和私有副本写入；**不是原算法推理证据**。
   - 组装：稀疏不连续 ID、大整数 ns 不丢精度；缺失/错误时间、元数据冲突、额外/缺失/错误尺寸/灰度图片、
     截断 sidecar、尾随字节、重复时间 ID、不互反 track、越界 NAME 均拒绝。
   - XMP：算法身份对照和完整旧/新最小组装对照。
3. 手工最小组件（2 图、2 点）→新 merge→validate_full(strict)：零问题。
   调用未修改 STG 的真实 `scene.dataset_readers.readColmap4dSceneInfo`，读入 2 帧/2 点，
   camera timestamps `[0.0, 1.0]`、point times `[0.0, 1.0]`；读入/组装本身 0.022 秒（不含 Python/torch import）。
   未用 mock 替代 STG loader；没有调用训练、渲染或 Gaussian 初始化。
   样本位置 `/tmp/colmap4d-components-smoke-y2ei64e_/model`。
4. 真实历史数据 `output/dog_megasam`：81 帧，pts_stride=32，导出 **165,240 点**；
   组装 strict_problems=0；STG 实际读入 **81 帧、165,240 点**。导出 50.714 秒、
   组装 7.666 秒，连同 STG 读入总计 **60.563 秒**（不含 import）。
   结果 `/tmp/dog-megasam040-smoke-ughz9qkx/`，约 300 MB，未写原 output。
   时间采用显式 synthetic_uniform 30/1 的测试策略；**不宣称原视频是 30fps，也不宣称恢复曝光真值**。
   这是“真实 MegaSAM 输出 → 组件 → 组装 → STG 读入”CPU 冒烟，**没有重跑从视频开始的 GPU 推理链**。

复现轻量测试（仓库根目录）：
```bash
PYTHONPATH=hololab/tests:packages/colmap4d/src /cloud/cloud-ssd1/envs/kiri/bin/python -B -m unittest test_megasam_components_040 test_colmap4d_components_020 test_colmap4d_xmp_010 -v
```
真实 STG 加载使用 `PYTHONPATH=SpacetimeGaussians:SpacetimeGaussians/thirdparty/gaussian_splatting`；
无需改 STG，调用 `readColmap4dSceneInfo(model_path, None, False)` 即可。真实样本执行脚本留在
`/tmp/megasam040-real-smoke.py`，结果摘要另存本报告同目录 JSON（不提交大样本）。

## 限制、保护和剩余事项

- 未重跑 DA/UniDepth/MegaSAM/RAFT/CVD GPU 推理；已验证的原生调用链被复用，真实缓存标准化与实际 STG 读入有证据。
  当前验收足以覆盖格式/适配；不能把 CPU tracker fixture 说成算法 E2E 成功。
- 首版拒绝 HDR 和未支持的色彩矩阵/范围；声明的 SDR 转换到 sRGB，缺失色彩标签明确记录 BT.709 limited-range 假设。
  不应用视频旋转 metadata；在同一像素方向上跟踪和导出。长视频 ffprobe/抽帧/复制有超时保护，复杂长片可能需后续扩展。
- 相机导出要求 native PINHOLE；STG 当前也仅支持 PINHOLE/SIMPLE_PINHOLE。组件 schema 能描述更广相机模型，不等于所有下游支持。
- 导出一次解压完整 depth tensor，再逐帧流式写点；merge 仍把模型加载到 RAM。81 帧 stride=2 约 4,200 万点，
  不能把 stride=32 冒烟的资源用量推广到默认密度。此机当前 236 GiB RAM / 228 GiB available，GPU 0/81920 MiB，盘余约 338 GB。
- 没有 UI 改动，因此无 UI 截图。没有发布、push、服务重启或 GPU 训练。
- `packages/colmap4d` 与 `packages/ml-sharp` git status 为空；规范 v1.0 不改。
  本次 hololab diff 仅有新增三 pack、profile 文档、三测试和实施证据。
  旧 merge@0.1.5、旧 MegaSAM@0.3.0、stg 系列、website 均未改。
- 父仓库 `SpacetimeGaussians` M、`packages/FreeTimeGsVanilla` M、`backups/` 是任务前已有，**非我所改**。
  父仓库两份旧调查报告、hololab/reports/a800-monocular-20261105 为之前会话工作，未纳入本次提交、未修改。
- 草稿删除已获本次明确授权；其它旧版本冻结。catalog 发布和工作流组装仍留待用户后续安排。
