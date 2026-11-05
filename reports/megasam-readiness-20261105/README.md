# MegaSAM 就绪检查与路径修复（2026-11-05）

包管理器检查的是声明依赖 colmap4d/ffmpeg，当前满足；节点准备还检查本地参数。检查范围不同，因此依赖满足与路径参数未填可以同时发生。prepare_params 的 required 字段实际保存所有路径值，包含 optional 路径，不能把它当作必填列表。

## 证据与修复

- `capabilities-before.json`：MegaSAM satisfied，colmap4d 1.0.2 / ffmpeg 4.2.2。
- `prepare-before.json`：MegaSAM unknown，prepare_params 提示 megasam_repo 空；run_blockers 使用误导性的“本地能力检查失败”。
- `analyze-before.json`：当前 API 将参数错误放在 issues，未返回 environment_checks。部署中的前端此时显示“尚未运行”，本次未复现用户先前截图的红色状态，不将截图伪称为该红色状态复现。
- `hololab/frontend/src/canvas/parameterReadiness.ts:6` 按 manifest 的 mandatory path 定义列出缺失字段；optional exposure 不算缺失。非法路径/连接问题显示“本地参数准备失败”；真实环境问题保留环境诊断。混合问题提示还存在环境问题。
- 同文件 `:37` 将 analyze 的 prepare_params 错误转为显示证据，保留能力报告及原 status，不更改检查结果；`hololab/frontend/src/App.tsx:3169` 接入当前 analysis，防止过期参数证据覆盖新配置。
- `hololab/frontend/src/canvas/nodeStatus.ts:98`、`AlgorithmNode.tsx:434`、`EnvironmentChecks.tsx:21` 使用分类文案。运行中/历史快照不被当前准备错误覆盖。
- 不修改 gateway/preparation.py、node/runtime.py、manifest/path_params.py，不改变 prepare_params 检查和拒绝启动规则。后端原始 run_blockers 文案仍原样留存；`hololab/frontend/src/api.ts:462,472` 在前端根据结构化环境证据分类弹窗错误，拒绝启动的判断条件不变。无 schema 的弹窗仅点名后端已证实的缺失字段，画布按 manifest 列全三个字段。

## 四个路径参数

| 参数 | 核实结果与写入值 |
| --- | --- |
| megasam_repo | 两种输入都必需；`/cloud/cloud-ssd1/Kiri4DGS/tryouts/mega-sam` |
| sharp_repo | 两种输入都必需；`/cloud/cloud-ssd1/Kiri4DGS/sharp-4dgs`，per-frame/video_to_colmap.py 存在 |
| tracker_python | 两种输入都必需；`/cloud/cloud-ssd1/envs/kiri/bin/python`，Python 解释器而非脚本 |
| exposure_metadata_path | 原 manifest 已是 optional:true，保留 null；video exposure_metadata 才强制需要；另外两档视频模式不需要；图片数组可用此文件覆盖时间 |

代码依据：`community_packs/megasam-track@0.4.0/run.py:360-361,386` 显式解析三路径，并用指定解释器启动 sharp wrapper；`run.py:33-40` 控制视频曝光元数据要求；`run.py:265-282` 控制数组 JSON/XMP/显式 frames_fps 优先级。`../sharp-4dgs/per-frame/utils/megasam_track.py:37,264` 检索仓库并通过 sys.executable 启动原生步骤。实际已安装 native extension 是 cpython-312，见 paths.json；kiri 解释器导入 torch、cv2、timm、droid_backends、lietorch、unidepth 成功，wrapper --help 成功。仅验证导入及帮助，不宣称完成原生推理。

`community_packs/megasam-track@0.4.0/manifest.yaml:57-72,102-110` 仅澄清硬依赖及曝光文件条件描述。没有将原本 optional 参数再“修成 optional”，没有发明 manifest 条件必填字段。版本保持 0.4.0；未发布首版按用户授权原位澄清，manifest 文本及代码身份因此改变。README 同步说明。

## 目标草稿修改

通过 gateway API 带 base_updated_ts 乐观锁保存 `single_video_4d`（9967b1f8-3646-41f5-a269-394b6d392169），实际节点 ID 是 `nmw38vr8y4`。仅修改三个路径、exposure_metadata_path=null、timestamp_mode=pts_estimate。GET 回读比较确认其它节点及全部连线不变。网关自动将三个绝对路径登记到该节点 local_params；不是额外人工改图。详情见 workflow-update.json。

`prepare-after.json`：目标 satisfied、issues=[]；`capabilities-after.json` 同样 satisfied；`analyze-after.json` issues=[]。这证明路径准备障碍解除，不代表端到端算法可运行。

**剩余时间输入问题：当前连线仍是 frame-extraction → MegaSAM 的 image array。pts_estimate 只对视频有效。没有 XMP/曝光文件时仍需用户显式给 frames_fps，或者改接原视频。已经向用户提出选择；未获答复，因此保留连线，不猜 FPS。** 没有启动 GPU 推理或训练，也没有运行该 workflow。

## 验证和截图

- Vitest：parameterReadiness/nodeStatus/EnvironmentChecks/NodeRunControl 加 api.requestIds 共 138 项通过。
- Python unittest：test_megasam_ports_040 + test_megasam_components_040 共 6 项通过，包含 manifest schema 加载、四路径规则、两种输入 CPU fixture 与组件组装验证。fixture 不是原生 MegaSAM 推理。
- tsc -b、vite build 通过；仅已有大 bundle 提示。产物 `/tmp/hololab-megasam-readiness-dist-20261105`。未替换 live dist、未部署、未重启 8828，部署由用户执行。
- 截图拍于保存参数前：before 是在线旧构建，after 是浏览器拦截静态资源的本地新构建预览；API 均来自在线网关，没有伪造 API。浅/深均展示节点 tooltip；after 明确列出三个缺失参数。

| 主题 | 旧构建 | 新构建预览 |
| --- | --- | --- |
| 浅色 | [before-light](before-light.png) | [after-light](after-light.png) |
| 深色 | [before-dark](before-dark.png) | [after-dark](after-dark.png) |

未修改其它 pack、workflow、sharp-4dgs、STG、website。未提交原有 dist.bak、其它报告、父仓库原有子模块变化。只提交本次源文件、测试、pack 文案和报告；不 push。
