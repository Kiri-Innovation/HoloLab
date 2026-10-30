# 草稿画布错误显示“未运行”：调查与修复

1. **结论与真实样本**

   根因是草稿画布把“最新 Run 的成员”当成“节点当前有效执行记录”。workflow GET 中的跨历史 `latest_run` 并没有进入状态 reducer。旧快照的 WS 更新还被最新 snapshot id 门禁挡住。并不是 `registry.py:1787` 的默认派发基准选择错误。

   以下是对本机 8828 HTTP 和 `file:/root/.hololab/gateway/hololab.sqlite?mode=ro` 的实际读取；workflow 为 `586915eb-0a2d-4566-b0f1-f6febd1957b3`，节点为 `freetimegs-init`。数据库现场已经继续推进，不能把今天读到的 `done` 写成仍在 `running`。

   |读取层|实际值|
   |---|---|
   |`snapshot_jobs` + `jobs`，快照 `951aaf1b`|job `282a3cd0-2279-4b3f-8fb0-71367fb908fd`，当前 `done`|
   |快照 `f7927cf9` 的该节点 jobs|`[]`|
   |当前最新快照 `20c1ea10`，`GET /api/snapshots/{id}`|该节点 `jobs=[]`、`graph.nodes[].latest_run=null`|
   |`GET /api/workflows/{workflow_id}`|该节点 `latest_run.job_id=282a3cd0…`、`state=done`、归属快照 `8f1b8c3a`（后续继承）|
   |旧服务 `POST /api/workflows/{workflow_id}/analyze`|`staleness.freetimegs-init={kind:self_dirty,title:尚无成功运行结果,changeReasons:[]}`|
   |新选择器，读取同一只读数据库、使用服务返回的有效配置|选中 `freetimegs-init / 282a3cd0 / done / 8f1b8c3a`|

   时间证据：job `started_ts=1793344208.0784855`，`f7927cf9.created_ts=1793344368.7029376`，job 的 `transition:done` 事件为 `1793344615.5685072`。中间有持续 progress 事件。即新快照创建时旧 job 尚未完成。

   用 `changed_nodes(f792.graph, 951.graph)` 比较执行配置，结果为 `set()`；两者的 init 参数、版本、执行设备等一致。当前 draft 相对它们的变化在其他列出的支路；新选择器也校验了 init 的上游来源，接受了该 job。这不否定之前其他归属因真实改动而正确失效的结论。

2. **完整旧链路与三个口径**

   以下旧前端行号对应修复前基线 **`de91c61`**，可用 `git show de91c61:<path> | nl -ba` 复核。未改动的 gateway 文件行号仍适用。

   ```mermaid
   flowchart TD
     A[App.onLoad 草稿画布] --> B[GET /api/workflows/id]
     B --> C[app.py get_workflow:2136]
     C --> D[workflows.py latest_runs_for_workflow:2067]
     D --> E[SQL 按 graph_node_id 跨历史 ROW_NUMBER]
     E --> F[graph.nodes.latest_run 有旧 job，但前端未用于 runtime]
     A --> G[GET /api/workflows/id/runs]
     G --> H[GET /api/snapshots/最新id]
     H --> I[app.py get_snapshot:2674]
     I --> J[registry.py JobsStore.list_by_snapshot:1497]
     J --> K[SQL WHERE sj.snapshot_id = ? 加本快照非 done 尝试]
     K --> L[最新快照 jobs 不含 init]
     L --> M[App latestSnapshotJobs → aggregateJobsToRuntime]
     M --> N[AlgorithmNode runtime 缺失 → 灰点、未运行]
   ```

   - **Run 成员**：`frontend/src/api.ts:355,369` → `gateway/app.py:2526,2674` → `registry.py:1497`。SQL 在 `registry.py:1530` 附近，按指定快照查询 `snapshot_jobs JOIN jobs`，另 UNION 本快照未成功尝试。该口径应继续保持。
   - **节点状态**：旧 `App.tsx:2461–2485` 只将第一个快照的 `snap.jobs` 写入 `latestSnapshotJobs`；`:1093` 聚合它；`:1333` 注入节点；`canvas/AlgorithmNode.tsx:806` 在没有 runtime 时返回“未运行”。旧 `App.tsx:695` 附近的 WS 门禁只接受 `p.snapshot_id === latestSnapshotIdRef.current`。`preparation.py` 旧 `analyze_execution:413–416` 也只取最新快照的 jobs。
   - **预览结果**：旧 `App.tsx:2433–2525` 最多回溯 8 个快照，过滤 `job.state !== "done"`，按节点首次成功填充预览；原 WS 产物回填也没有经过当前草稿来源校验。这既可能漏掉旧结果，也可能显示已经过期的成功结果。
   - **workflow GET 的提示**：`app.py:2146` 调 `workflows.py:2067`；SQL `workflows.py:2108` 附近按节点分区，排序 `snapshot.created_ts DESC`、parent 优先、`job.created_ts DESC`，**没有成功状态过滤**。它是历史提示，不是经当前草稿有效性校验的画布证据。

   候选逐项判定：

   |候选|验证结果|
   |---|---|
   |(a) 8 次窗口|事发 `f7927cf9` 截止时 `951aaf1b` 排第 **5**，所以不是此次灰点的原因；本次读取时它已排第 **12**，确实会越过旧预览窗口。|
   |(b) 只认最新快照|**直接根因**：前端 runtime + WS 门禁 + `/analyze` 都仍是此规则。|
   |(c) 只认成功|仅旧预览回填如此，独立导致 running 没有历史预览；workflow 查询和状态聚合本身没有过滤 running。|
   |(d) 多快照并行|规则冲突的触发条件。不同支路生成更新快照即可触发，和恰好有 3 个无关。现场时间记录可确认至少两条旧支路跨过该创建时刻；未把无法重放的完整“三个 running”现场当作本次实测。回归测试明确覆盖三快照并行。|
   |(e) 其他|后端 analyze 的同一最新快照假设会额外给错误失效提示；只改灰点不能解决它。|

3. **修法**

   - `hololab/gateway/canvas_jobs.py:8`：纯选择器。按有效配置比较节点、连线及双图下游闭包；校验实际 job 参数；按拓扑校验上游 job 身份一致；只纳入被选中 parent 的 shards。新失败不会退回旧成功，无效的新记录也不会被更老成功覆盖。
   - `hololab/gateway/canvas_jobs.py:61`：一次全历史排名 SQL，无 `LIMIT 8`、无成功过滤；仅加载提供获选 parent 的快照。没有改 `snapshot_jobs` 表或持久化归属。
   - `hololab/gateway/preparation.py:418,436,482`：`/analyze` 返回 `canvas_jobs`，失效判断复用这份经过来源验证的证据。最新快照仍用于配置 diff。
   - `hololab/frontend/src/App.tsx:749,1002,2128`：草稿 runtime、边上的输出来源、聚合预览及 shard 预览统一用 `canvasJobs`。WS 只更新已校验的 job id；异步预览查询有取消保护，不再把旧完成事件按节点名直接塞回画布。只读 SnapshotCanvas 保持本 Run 的成员。
   - `hololab/frontend/src/canvas/executionAnalysis.ts:33`：请求完成后每 5 秒刷新，发现旧快照中新建的 job/shard、状态变化；已有获选 job 的 WS 更新即时生效。慢请求不会被周期计时器反复取消。刷新新身份有约 5 秒加请求耗时的延迟。

4. **验证**

   - Python：`tests/test_canvas_jobs.py tests/test_execution_preparation.py tests/test_workflows.py`，**36 passed**。包括真实 SQLite 排名查询越过 12 个快照、三快照并行、旧 running、参数/版本/删节点/改连线及下游失效、上游生产 job 换代、失败不回退旧成功、shard 代际隔离，以及 analyze 返回状态一致。
   - 前端 Vitest：**63 files / 716 tests passed**。
   - 前端 `npm run build`：通过；保留已有大 bundle 提示。
   - Ruff：本次 Python 文件通过。
   - Playwright `e2e/canvas-history.spec.ts`：**3 passed**，分别第 2、8、9 个历史位置。真实 Chromium 中验证 running → done → 失效后未运行，且不请求旧快照补预览。HTTP 使用隔离 mock；SQLite 查询由后端测试和现场只读运行另行验证。没有宣称在生产服务中派发了新的真实训练 workflow。
   - 为运行 Chromium，下载浏览器并将缺失依赖包解压到 `/tmp/hololab-canvas-browser-libs`；未安装这些包到系统、未重启服务。浏览器用该目录追加到 `LD_LIBRARY_PATH` 运行。

5. **重启后验证**

   1. 由用户重启 gateway；刷新浏览器，确保使用本次构建的前端。
   2. 打开 `/w/586915eb-0a2d-4566-b0f1-f6febd1957b3`。在 Network 中查看 `POST .../analyze`：`canvas_jobs` 应含 `freetimegs-init / 282a3cd0`，其当前状态应为 `done`，画布不再“未运行”。若之后真实配置/来源变了，应以新有效性结果为准，不能强制显示这个 job。
   3. 新建测试 workflow，分两条独立支路；启动 A 的慢任务，在它运行期间单独执行 B 以产生新快照。回草稿并刷新：A 应继续 running；B 不应覆盖 A。反复执行 B 超过 8 次，A 仍应保留。A 完成后应变为 done，并显示该执行的产物。
   4. 修改 A 的参数或其上游配置/连线：A 及其依赖结果应失效，旧预览清除；B 不受影响。仅重跑上游但配置不变时，下游不能搭配旧来源结果冒充新结果。
   5. 打开最新 Run：其成员仍是该快照实际归属，不应该因草稿跨历史显示而新增归属。

范围：没有修改 `registry.py` 的已有 `any_of` 改动；没有修改 `packs/`、`website/`、其他会话的 community packs、报告或测试；没有发布 registry 内容、没有 push、没有重启服务。
