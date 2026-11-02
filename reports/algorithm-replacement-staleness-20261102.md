# 替换算法的即时变化提示

## 复现与根因

在真实 App 的 Playwright 测试中加载带已完成任务的 demo@1.0.0 草稿，等待初始分析完成，右键 → 替换为算法 → demo@2.0.0。让新版本的 `/analyze` 请求保持未完成：修复前版本已经显示 2.0.0、草稿也已保存，但节点仍显示旧的完成结论，Runs 没有变化提示。延迟用例修复前在黄色状态断言失败；快速返回用例修复前通过。

关键结论：**本次复现中 key 变了，且请求已触发；错误在等待期间继续展示旧结论。**

数据流：

- `App.tsx:2675` 替换节点 → `toGraph`（1883–1943）读取新 pack 身份。
- `analysisInputs.ts:22` 将算法名称、版本放进语义图；`executionAnalysis.ts:31` 构造新 key。
- `executionAnalysis.ts:73` 因 key 不同将 current 置空，但原来的 `current?.data ?? previous?.data`（现 77–79 行）无条件返回旧 staleness 和 diff。
- `App.tsx:2139` / `2241` 消费上述旧 diff / staleness，因而仍显示“没有变化”。请求慢、失败或被新的 baseline 更新取消时，这段时间可以很长。刷新会清除 hook 的旧成功结果，因此“刷新后能识别”不能单独证明 key 没变。

A800 8828 只读观察补充：打开现有 31 节点流程时看到了 `/analyze` 发出、被取消并重新发出，在该次 20 秒观察内没有成功返回。没有修改这个工作流，也没有重启服务。确定性根因复现采用隔离的 API 夹具；不声称在用户正在运行的工作流上完整重演了原始操作。

## 五个方向逐项验证

1. `toGraph` 依赖数组包含 nodes，analysisGraph 在 render 调用它；浏览器新分析请求体确实包含 2.0.0。
2. `samePorts` / `samePackage` 只拒绝端口不兼容或相同身份。新版本正常替换；原单测覆盖不兼容 guard，浏览器覆盖禁用当前版本、隐藏不兼容候选。
3. 关对话框没有卸载分析 hook，也不改变 key；新版本请求在关对话框后正常发出。200ms 防抖不是丢请求原因。
4. `useDraftAutosave.ts:237` 监听序列化草稿变化，800ms 后调用保存并携带 CAS 时间戳；独立于分析返回。
5. `lastHydratedWireRef` 仅为 toGraph 提供 recipe_dependencies，算法身份来自实时 nodes；不变的 ref 不会阻止签名更新。改参数也只是 setNodes，并没有额外刷新调用；连线走 edges → 同一签名通道。

## 修改

新增 `pendingAlgorithmChanges.ts`：分析尚未给出当前结论时，将当前算法身份与上次成功分析比较。仅对确定的名称/版本变化添加临时 dirty 状态与 diff，并传播到下游；正在运行的任务显示使用旧配置。保留原运行证据和有效参数，不把临时提示作为可执行配置。当前后端分析成功后完全采用后端结果。

使用 canonical package identity，别名不产生假变化；没有身份变化时保留结果引用。hook 对临时结果 memoize，避免更新节点造成循环。没有修改 App.tsx、toGraph、analysisSignatures、自动保存或后端调度。

## 验证

- Vitest：4 个文件、31 项通过（新增 pendingAlgorithmChanges 4 项）。覆盖签名、直接/下游变化、别名、撤销、运行中证据、已有原因、循环和新分析结果接管。
- 替换浏览器测试：快速响应、挂起响应两种情况通过；挂起时已显示黄色及 Runs 提示，释放响应后转为“未运行”，刷新后保留新版本。
- analysis-stability 的 semantic event replay / SWR / profiler 测试通过，包含晚到响应淘汰、失败和无关进度更新。
- 同文件 real App WebSocket replay 既有用例失败：期望 100/100，实际为读取中。将 executionAnalysis.ts 临时还原为 HEAD 后复跑，同样失败，随后恢复本次修改。该夹具的 analyze 响应未提供 canvas_jobs；本次没有修改这个独立测试问题。
- `npm run build` 成功；仅现有 bundle 大小提示。
- `git diff --check` 通过。

## 实际落盘确认与清理

额外创建临时工作流 `0926ceed-aa74-4c43-9f94-0ccc458116c8`，通过 8828 的浏览器界面将已安装的 birefnet-mask 0.1.0 替换为 0.1.1。自动保存请求和 GET 读取均走真实 A800 Gateway，刷新后仍显示 0.1.1。这个验证隔离了 WS、分析响应和设备列表，专门验证真实保存链路，不执行算法或创建 run。

验证后删除临时工作流，再 GET 确认 404。没有发布任何 registry pack，没有触碰 4090，没有重启 hololab；保留工作区原有改动。
