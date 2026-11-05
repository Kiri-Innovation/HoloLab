# MegaSAM 0.7.0 direct colmap4d and split 0.1.0

2026-11-05，A800 本机真实验证。未改 colmap4d 库、原 merge 包、前端或其它 pack；未重启服务、未 push、未发布中央注册表，也未停止其它任务。

## 实现选择

- 新增 `tryouts/mega-sam/packs/megasam-track@0.7.0/`，仅输出 `model: colmap4d`。保留 0.6.1 供历史快照使用。原生推理和组件 exporter 不变，组件现在位于私有 scratch，导出后直接调用 merge 0.3.0 的 `assemble()`。
- 选择**直接调用固定版本代码**，没有抽共享实现。`vendor_merge_030/{merge_colmap4d.py,strict_legacy.py}` 与发布源文件逐字节一致，provenance.json 记录 SHA-256，携带 Apache LICENSE，契约测试核对源文件。避免跨独立仓库的部署路径依赖，也避免修改已发布 merge 的性能和代码身份。代价是未来升级须显式同步 vendor；不是另外实现一套组装逻辑。
- 新增 `col4d/colmap4d-split@0.1.0`：输入 model，三个输出与 merge 输入的名字、tag、storage、arrayed 完全对齐。`read_point_arrays` / `read_image_headers` 读取几何和头部；随后原字节复制模型表、sidecar、元数据和图片。无 ID 重编号、无 ns 重算、无轨迹或观测重写、无硬链接。
- 支持 sparse/0、sparse、根目录布局，bin 优先；要求 merge 所需的 timed component profile，包括两份时间表、time_meta 和图片。一般 colmap4d 的 optional-sidecar 模型缺少这些时会明确失败。split 不是 full validator，完整不可信数据认证仍使用 merge strict_validate。非规范/text 输入的再次 merge 可能按原 merge 规则归一化；不承诺任意文本往返逐字节。

## 三端口方案与实际开销

采用 A：三个固定端口、零参数，消费者连接所需组件。但核实后**不能声称未连接端口不产生开销**：

- `hololab/manifest/render.py:245` 为 manifest 的所有 outputs 分配路径，没有连线筛选。
- `hololab/node/runtime.py:2776` 的 `_register_output_handles` 对所有输出执行契约检查、目录大小扫描并注册 handle。
- 真实 split job 以 `/api/jobs/run` 单独派发，没有任何图上消费者，仍返回三个 output handles（split-job.json）。

因此未连接端口仅不引发下游消费/传输；本地三组件仍全部生成。B 在现有静态输出注册契约下不能简单省略未选择目录，容易产生空端口语义，也不能自动知道连线。A 可行且保持零参数；代价已用真实耗时量化。

## 真实 4199 万点往返

输入为指定 job `a31dba7e-9ca5-4c04-8d4b-c915a5a30937` 的 model，41,990,400 点 / 81 帧。直接 merge → split job `be88568f-f75e-496a-96c5-e7ea1f19f446` → merge job `93a21130-273b-4c42-a9ad-919944f7c2f1`。

`roundtrip-equivalence.json`：**88 个内容文件、4,289,158,325 bytes，逐文件 SHA-256 全部一致**。包括 81 张图片、5 份二进制表、time_meta 与完成 marker；仅排除 HoloLab 每 job 不同的 `.hololab-metadata.json`，未改写该文件。

| 项目 | gateway started → done | 算法内计时 | 进程峰值 RSS |
| --- | ---: | ---: | ---: |
| split | 5.3909 s | 5.1651 s | 3,932,412 KiB（3.75 GiB） |
| 再次 merge 0.3.0 | 12.4566 s | 12.2156 s | 4,454,560 KiB（4.25 GiB） |

相较既有 12.16 s 快 merge，当前 12.46 s 同量级；未出现共享重构回退。这里是实际工作负载计时，没有控制冷缓存；并发任务可能影响耗时。

关键大文件 SHA-256（两侧相同）：

- points3D.bin：`61c078016d2829e656c6866a32f708a21412e32abcee28c0679c1b0354cef470`
- images.bin：`cfc3fd5904a45f02dc74c017f9d153298f8dc15c6831b1fd3144e4672b6eace7`
- points_t.bin：`34e879ceada5cc7b35e49070c7d7b1e16c26a7dbd1d0f1a1e6e3c5ca0e683f5f`

## Workflow 迁移

workflow `9967b1f8-3646-41f5-a269-394b6d392169`：

- 前：single-video-source → frame-extraction → megasam 0.6.1（三端口）→ merge 0.3.0；4 节点 / 5 边。
- 后：single-video-source → frame-extraction → megasam 0.7.0（model）；3 节点 / 2 边。
- 现场 merge 后没有其它下游，因此新 model 为终点；没有虚构或增加训练节点。
- 保留输入来源、两个上游节点、全部 MegaSAM 参数。移除节点 nmw3rj68z2 与其三条输入边，更新 recipe_dependencies，清除 MegaSAM 旧端口契约让 gateway 重算。历史快照不改，merge 包不删。
- 使用最新 updated_ts 的 CAS 保存，未绕过并发修改保护；保存后回读核对参数及版本。workflow-before/after.json、migration-request/response.json 为证据。
- 新链在线 prepare HTTP 200，run_blockers=[]、环境检查 satisfied。真实推理用同一完整输入 handle 单独派发，不把 ad-hoc job 冒充新 workflow 快照运行。
- 截图：workflow-before.png、workflow-after.png、megasam-inspector.png。Inspector 明确显示 0.7.0 与单 model 端口。未改前端。

## Schema 与回归

schema.log：两个 manifest 均通过真实 `load_manifest` 校验。

- split-tests.log：3 个方法，包括单/多 track × 3 种目录布局的往返、稀疏非连续 ID 与大 int64 时间、文本字节保留、输出重叠和缺图拒绝、独立 inode；快路径禁用对象 readers 后仍通过。
- megasam-tests.log：5 个测试通过，覆盖原时间策略与 vendor 源码哈希一致性。
- merge-regression.log：原 merge 5 个方法通过，包含已有 20 种破损拒绝、fast/strict 等价及回退；原 pack 源码未改。

复现脚本：verify_roundtrip.py、capture_native.py、verify_megasam.py；原始 request/job/log/handle JSON 均随报告保存。数据副本保留在 output/hololab 和 output/colmap4d-split-070，不纳入 Git。

## MegaSAM 真推理与直出等价

新包真实 job `587cae81-d96b-4abe-85c4-f64a7752713d`：同原 0.6.1 大任务的完整输入 handle 和参数（skip=1/max_frames=0/pts_stride=2/frames_fps=30），执行完整 Depth-Anything、UniDepth、MegaSAM tracking、RAFT、CVD 链；**81 帧 / 41,990,400 点**，没有缩小 smoke 输入或 mock 推理。

- gateway started → done：**369.9217 s（6 分 09.92 秒）**。
- 主程序计时：369.6388 s；其中固定版 merge assemble：**12.3457 s**。
- 主进程记录峰值 4,486,404 KiB；这不是所有算法子进程或 GPU 的全链峰值，未冒充完整显存/内存测量。
- 为严格隔离“输出组装变化”与“GPU 算法再次推理的数值差异”，在本 job 正常成功清理 scratch 前，只读复制它的最终 native frames、COLMAP 和 original_resolution.npz。随后**执行原 0.6.1/run.py 的 export() 原函数**，得到三组件，再执行未改的 merge@0.3.0 CLI。
- `megasam-equivalence.json`：新直出与上述旧路径 **88 个文件 / 4,289,158,325 bytes 的 SHA-256 全部一致**，同样仅排除 per-job `.hololab-metadata.json`。
- 这是一次真实推理、两条真实导出/组装路径的严格等价检验；不声称另一次独立 GPU 推理或早先旧 job 的坐标结果必定字节确定。

## 遗留与边界

本任务要求的实现、真实大输入往返、真实 MegaSAM 直出对照、schema/回归、截图、指定草稿迁移均完成。新包由本机自动发现，无需重启。旧 0.6.1 和 merge 各版本保留；未发布到中央注册表，未 push。

split 生成全部组件而非按连线懒生成，是现有框架边界；目前不修改框架。独立真实 GPU 重建的可重复性、缺时间表的宽松模型补时、非规范文本的字节级归一化均不属于本次承诺。验证产物保留，可按报告中的路径复核，不自动清理用户或其它任务产物。最终草稿回读见 workflow-final.json。
