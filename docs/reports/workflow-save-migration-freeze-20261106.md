# 迁移后工作流 500 / 409 诊断与修复（2026-11-06）

结论：已定位维护冻结与工作流乐观锁两条不同链路。普通保存可以成功；派发创建 job 被迁移 claim 拦截。旧浏览器持有过期版本时，409 是并发保护，不能用自动换版本号覆盖解决。

## 证据与请求差异

当前日志中最新相关栈：

```text
gateway/app.py:3322 dispatch_single_node
gateway/execution.py:1872 _dispatch_graph_node
gateway/execution.py:294 _prepare_fanout
gateway/registry.py:973 _write
sqlite3.IntegrityError: artifact migration maintenance freeze
```

数据库只读检查：触发器冻结 handles 注册/更新/删除、cleanup claim、job 创建/派发；没有冻结 workflows 更新的触发器。不能仅凭 cleanup_pending 判定冻结，真正依据是 artifact_migration_claims 行。

发现 fa13e786 的 claim 仍在；维护进程 PID 3078774 还持有 owner.lock、manifest.jsonl 和目标大文件的 FD，读取位置持续推进。这是执行中的同步 finalize，不可以直接删 claim。CLI 的 120 秒超时不会停止服务端校验，worker_running 原来也只反映迁移复制任务，不能说明 finalize 是否执行中。此时源备份已被外部回收，但 state.json 仍为 compatibility_ready，旧 finalize 接下来会因备份缺失失败并留下 claim。

当前 /api/workflows 保存路由与 /api/workflows/{id}/nodes/... 派发路由不同。前端先保存、再派发；curl 仅保存不能复现派发冻结。用户截图 URL 被截断，无法取得其原始请求体和原始 409 响应，因此不把截图中的每个请求都认定为普通保存，也不声称已抓取用户旧标签页。

模拟前端完整字段：workflow_id/name/graph/base_updated_ts/origin，当前真实图 47 节点、64 条边，内容保持不变。HTTP 客户端连续两次保存 200。真实 Chromium 153 使用同源 fetch 也返回 200；重放旧版本返回 409 workflow_conflict。未知 pack 的 hydration 警告不阻止当前完整图保存。

前端 useDraftAutosave 遇 409 会停止保存并阻止 Run，WorkflowConflictDialog 提供“暂不刷新”和“重新加载”。没有自动更新 ts 后强行重试，符合保护其他会话修改的约定。诊断中的同内容保存也会推进 updated_ts；已有旧标签页需保留必要未保存修改后重新加载。

## 修复

提交：2f6effc（fix: recover finalized migrations and release maintenance freezes）。

- finalize 在目标全量 SHA、manifest、目录身份、兼容软链和源盘挂载验证后，允许恢复“备份已消失但 DB 未完成”的终态登记。
- finalize 校验/回收失败时释放维护 claim；保留 cleanup_pending 与磁盘现场，允许检查后重试。
- cancel 将所有单元一起置为 aborted，清理 retryable_failed 残留并释放租约。
- status 新增 filesystem_locked，能看到跨进程文件锁。
- CLI finalize/rollback 不再受 120 秒请求超时截断。
- 网关将 maintenance freeze 映射为结构化 503 + Retry-After，避免误报 500。此异常处理改进随主网关下次正常重启加载；本次不为它重启主服务。

## 验证与现场恢复

Python：61 项通过；覆盖缺失备份恢复、损坏目标拒绝并释放冻结、目标替换拒绝、兼容软链缺失拒绝、取消单元终态、跨进程锁显示、维护错误转换，以及既有迁移和工作流 CAS 回归。恢复测试在临时 DB 中验证可再次创建 job，不会提交 GPU 任务。

恢复通过 MigrationService.cancel/finalize 服务代码执行，未写临时 SQL 修补业务状态；只读 SQL 用于诊断与验收。等待旧进程文件锁释放后才进入恢复，目标仍执行全量验证。保留迁移记账、manifest 和回执审计，不删除历史记录。

前端 Chromium E2E：8 项全部通过（保存串行化、失败重试、409 停止写入与阻止 Run、冲突对话框重新加载/暂不刷新）。命令：

```bash
npx playwright test e2e/workflow-conflict.spec.ts e2e/draft-autosave.spec.ts --workers=1 --output=/tmp/hololab-workflow-fix-e2e
```

独立维护 API 已在旧执行退出后重载为新代码；主服务没有重启。新 status 正确展示 worker_running=false、filesystem_locked=true，表明恢复代码正持有文件锁进行校验。最终恢复结果如下。

## 最终验收（2026-11-06 14:22 UTC）

- 目标 45,518 个普通文件、448,202,716,327 字节全量 SHA 与 manifest 一致；31,404 个软链按既有 missing 基线校验通过。
- fa13e786-b60c-4377-92ba-80a6101e9039：completed；91 个单元 completed；别名 source_reclaimed。
- 3bbc68eb-6d1c-4640-a035-2118ce3cde20：aborted；110 个单元全部 aborted，原失败原因保留作审计。
- artifact_migration_claims：0；filesystem_locked=false；保留迁移历史、manifest 和回执。
- 官方 CLI 再次 finalize --delete-source 返回 HTTP 200 / completed（幂等）。
- 解锁后真实 Chromium，同源、完整 47 节点 / 64 边图，最新版本连续保存 200 / 200；重放旧版本为 409 workflow_conflict；health=200。
- 主服务 PID 3251047 未变，没有重启主网关/node，没有实际派发 GPU 训练或发布注册表内容。派发冻结解除依据是 claim=0、触发器条件不再成立，另在独立测试 DB 中验证恢复后可再次创建 job。
- 本次源备份已不存在，恢复只做校验与登记，没有再次删除产物或 SSD2 数据。

用户需要：先保留浏览器未保存修改，再点击冲突提示中的“重新加载”，取得当前 updated_ts 与图；之后保存/派发。不要通过反复点击 Retry 或换成服务器版本号强行提交旧图来绕过 409。

限制：未接入用户原始浏览器标签页，不能逐条归因截断截图中的原始请求。未知 pack 版本仍属于工作流依赖问题，未擅自升级或替换 pack。503 维护错误提示已提交、测试通过，随主网关下次正常重启加载；当前故障通过完成迁移、释放 claim 即已解除。
