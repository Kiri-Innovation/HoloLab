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
后续阶段和验证证据将在本报告补齐。
