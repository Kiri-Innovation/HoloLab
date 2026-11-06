# 官方产物迁移工具：实现与生产验收（2026-11-06）

迁移工具已实现，并完成当前 workspace 的生产切换。源实体完整保留，
操作状态为 `cleanup_pending`；本轮没有授权或执行源数据删除。

## 生产结果

- 操作 ID：`fa13e786-b60c-4377-92ba-80a6101e9039`。
- 旧根：`/cloud/cloud-ssd1/Kiri4DGS/output/hololab`。
- 新主根：`/cloud/cloud-ssd2/hololab`。
- 旧根已成为指向新根的兼容软链，旧根仍在 `legacy_workspace_roots` 中。
- 源备份：`/cloud/cloud-ssd1/Kiri4DGS/output/.hololab-migration-source-fa13e786-b60c-4377-92ba-80a6101e9039`。
- 91 个登记存储单元，10,544 个关联 handle，路径与 location_version 全部核对通过。
- 45,518 个普通文件，448,202,716,327 字节（约 448.2 GB 逻辑数据）完成校验。
- 31,404 个软链；原有缺失目标 4,015 个保持原状，新增断链 0 个。
- 外部依赖共 97 个；保持外部指向，没有解引用复制，也没有宣称它们已自包含。
- 原有缺失目标中，15 个直接指向外部，4,000 个位于历史 scratch 并指向内部缺失文件。
- 迁移 claim 已释放；预览缓存维护标记已清除；新作业将使用新主根。
- 跨两个 workflow 的 6 个旧预览 URL：Range 返回 206，前 256 字节与切换前一致。
- 连续 192 次网关预览请求均返回 206，内容匹配；实际缩略图生成返回 200。
- 每个普通文件的完整 SHA-256 校验记录保存在目标盘操作清单中；URL 抽检是额外的服务验收。

生产验收的结构化证据见 [JSON](artifact-migration-20261106.json)。

## 分阶段提交和测试

| 阶段 | 提交 | 验收 |
|---|---|---|
| 数据模型、计划、位置 CAS 和持久化写入栅栏 | `3d3f8fb` | 2 项针对性测试 |
| 文件树复制、软链映射、校验、回执、交换与源保留 | `59a385c` | 累计 13 项针对性测试 |
| 本机鉴权 HTTP API 与 CLI | `b318994` | 累计 18 项迁移测试；69 项既有路径/Library 回归 |
| 故障恢复、缺失依赖清单、取消、显式回收 | `d1d4bc7` | 累计 24 项迁移测试 |
| 登记兼容、过期计划、主根收敛、元数据持久化 | `b3f73b5`、`b31b1f1` | 既有 handle 登记回归恢复；稀疏文件/权限测试 |
| 提交/交换崩溃重放、回收保留新数据 | `bcd46b1`、`8a87c7b` | 故障边界与回收专项测试 |
| 真实迁移发现的缓存写入冻结、恢复哈希复用、单产物预览命名空间 | `65e3631` | 50 项迁移与路径测试；完整选定回归 189 项通过 |
| 回滚意图恢复、缓存兼容、拒绝回滚时释放临时栅栏 | `e110c30` | 37 项迁移专项测试；最终选定回归见下 |

最终选定回归 **191 项通过（58.00s）**，新代码静态检查通过。命令覆盖以下模块：

```sh
.venv-runtime/bin/pytest -q \
  tests/test_artifact_migration_api.py tests/test_artifact_migration_files.py \
  tests/test_artifact_migration_store.py tests/test_artifacts.py \
  tests/test_artifact_lifecycle.py tests/test_snapshot_delete.py tests/test_handles.py \
  tests/test_handle_lookup_api.py tests/test_handle_deleted_flag.py \
  tests/test_handle_display_meta.py tests/test_workspace_roots.py \
  tests/test_library_transfers.py tests/test_library_management.py \
  tests/test_library_gc.py tests/test_gateway_orphan_reconciliation.py
```

测试使用独立临时目录的小数据；没有训练任务、GPU 运算或注册表发布。
回滚和实际删除源备份的路径在测试中验收，生产源备份没有删除。

## 真实运行中的两次安全阻止

开始实施时手工 `cp -a` 仍在运行，随后自然结束，工具没有终止它。
最初的源节点空闲，但开发期间另一个会话提交了 BiRefNet 批量作业。
第一次 run 因有待运行/运行作业被拒绝，未复制或提交；作业自然排空后重建计划。

等待期间，先前的目标副本从文件系统消失，目标盘恢复为空盘。这个变化不是本工具执行的。
因此生产第一次复制没有复用原来的 251 G 手工副本。已有副本接管及错误副本拒绝/修复由小数据测试覆盖。

第一次完整复制结束后，提交前源快照检查发现浏览器新增了 9 张缩略图，停止在提交前。
没有 handle 路径改变，也没有旧根切换。随后补上预览生成写入栅栏并恢复同一个操作。
恢复时，已有目标文件全部重新读取验证；源哈希只有在本操作清单记录的设备、inode、模式、大小、
mtime、ctime 完全一致且迁移 claim 保留时才复用。9 张新增缓存重新复制并校验，共新增 11,008 字节。
源快照复查通过后才提交全部路径并交换旧根。

## 服务重启说明

数据切换后，节点文件服务直连与文件内容正常，但旧主进程的反向隧道出现持续的间歇性 502 和提前 EOF。
没有可用的仅重建隧道池管理接口；仓库中已有的修复尚未加载到这个长期运行的进程。
按任务书“除非必要且说明”的例外，在确认没有待运行/运行作业后执行了一次受控主服务重启。

- 旧主 PID：2424289；新主 PID：3030552。
- 使用 `hololab stop`，随后启动 `hololab start --host 0.0.0.0 --workspace-root /cloud/cloud-ssd2/hololab`。
- 主服务 Screen：`hololab-migrated`。
- 重启后节点、健康检查、6 个预览样本及 192 次连续请求全部通过。
- 根配置热改另有文件服务子进程重启；没有终止任何运行中的作业，也没有接触 4090。

## 可用 CLI 和 API

维护服务监听 `127.0.0.1:8830`，Screen 为 `hololab-migration-api`。
鉴权凭证保存在 `~/.hololab/migration.token`，模式 0600；不要将内容放到命令参数中。
迁移代码通过正式 Database 服务写入，手工检查脚本只读 SQLite。

```sh
.venv-runtime/bin/hololab artifacts migrate status fa13e786-b60c-4377-92ba-80a6101e9039
.venv-runtime/bin/hololab artifacts migrate finalize fa13e786-b60c-4377-92ba-80a6101e9039
```

第二条命令已实调：默认保留源数据，状态仍为 cleanup_pending。
只有用户另行确认后才可调用带 `--delete-source` 的 finalize；本轮未调用。

单产物使用 `plan --node NODE --handle HANDLE --target-root TARGET_WORKSPACE`；
目录使用 `plan --node NODE --path SOURCE --target-root TARGET_WORKSPACE`；
整 workspace 使用 `--workspace --source-root SOURCE`。
源 workspace 相对路径会保留；计划可能扩大选择以覆盖祖先目录 handle 及共享分片。
然后 `run PLAN_ID`，或使用 `status / resume / cancel / rollback / finalize`。

| HTTP | 路径 |
|---|---|
| POST | `/api/artifact-migrations/plan` |
| POST | `/api/artifact-migrations`，body 为 plan_id、plan_digest、idempotency_key |
| GET | `/api/artifact-migrations/{id}` |
| POST | `/api/artifact-migrations/{id}/resume`、`cancel`、`rollback`、`finalize` |

读取实际生产状态的 HTTP 示例：

```python
from pathlib import Path
import httpx

with httpx.Client(
    base_url="http://127.0.0.1:8830",
    headers={"Authorization": "Bearer " + Path("~/.hololab/migration.token").expanduser().read_text().strip()},
    trust_env=False,
) as client:
    response = client.get("/api/artifact-migrations/fa13e786-b60c-4377-92ba-80a6101e9039")
    response.raise_for_status()
    print(response.json()["state"])
```

## 简化和剩余风险

- 当前是同节点、本地文件系统、节点作业排空后的版本；没有跨节点和运行作业在线迁移。
- workspace 以多个单元组成冻结 envelope，准备后一次提交全部 handle；没有独立逐单元部分提交。
- 断点是完整文件边界，未完成的大文件会重拷；已完成目标文件在恢复时仍完整重读验证。
- 未重写文件内容中的绝对路径；仍依赖旧根兼容入口，不能随意移除旧根路径。
- 外部依赖没有搬走，不能据此卸载整个旧盘；已有缺失依赖没有修复。
- 无法冻结任意外部写入进程；源变化仍会阻止提交。没有做断电级实机故障注入。
- 跨边界 hardlink 共享、特殊挂载、ACL/所有权跨平台及恶意目录竞态不宣称全面支持。
- 回滚会拒绝原迁移内容被改写、普通新数据出现或源已开始回收；正常新增预览缓存可以保留。
- 本轮没有生产回滚或删除源备份，没有做浏览器视觉截图验收；已做实际 HTTP 文件与缩略图验收。
- SSD1 仍约 98%，因为源备份完整保留；SSD2 约 416 GiB 已用、518 GiB 可用。后续新作业写新盘。

详细实现约束见 [设计与操作说明](../design/artifact-migrations.md)。
