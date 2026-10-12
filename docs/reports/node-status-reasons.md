# 节点状态提示：区分停止、失联、中断和失败

## 结论与根因

不是任务同时处于两种状态，而是两段展示逻辑给了相互矛盾的解释。
`AlgorithmNode.tsx:588` 把运行状态和陈旧性一起传给 `nodeStatus`。
`staleness.ts` 原来的“未产生完成结果”分支只认识 failed/cancelled/orphaned，
把 interrupted 和其它未知状态都写成“尚未运行”；`nodeStatus` 又无条件附加其 title。

修复在 `staleness.ts:347` 附近：明确识别 interrupted、未知状态，只有没有运行状态才说尚未运行。
新增 `changeReasons`，把配置变化和“为什么需要重跑”的运行结论分开；
`nodeStatus.ts:23,52` 只补充配置证据，不重复拼接另一段运行结论。
因此 orphaned 也不会同时要求“等待同步”和“立即重跑”。原有陈旧判定、传播、调度和状态颜色不变。
取消状态优先于旧参数提示保持灰色，配置变化仍可单独补充。

## 状态模型（代码事实）

| 状态 | 判定与转移 | 能否判断发起者 |
|---|---|---|
| cancelled | `gateway/app.py:3912` 的停止处理发出 job_cancel，并记录 CANCELLED；`app.py:3594` 收到 reason=cancelled 也归入该状态。父任务停止会级联分片（`execution.py:452`）。 | 能知道收到停止指令；不能知道是不是当前用户，记录没有操作者字段。 |
| orphaned | 执行连接断开时 `app.py:3091` 调用标记；gateway 启动也先标记待核实（`registry.py:616`）。心跳失联阈值 45 秒（`app.py:133,3750`）。 | 这是连接/恢复状态，不等于算法报错，也不能断言设备物理掉线。任务可能仍运行。 |
| interrupted | 重连时设备未认领任务（`app.py:4053`），或失联超过恢复宽限期（`registry.py:698,737`，宽限 300 秒见 `app.py:143`）。 | 当前字段未细分这两种原因，不能从该状态断言用户主动停止。 |
| failed | job_fail 的其它原因进入 FAILED（`app.py:3594`）；非零退出通常是 algo_error，部分退出码归 oom（`node/runtime.py:1762`）；另有输入/系统错误。 | 能区分记录的失败类别，但不是所有 failed 都是算法代码错误。 |

`gateway/jobs.py:57-102` 的状态机允许 orphaned 回到 running，或进入 done/failed/cancelled/interrupted。
cancelled/failed/interrupted 是终态，不会再互转。`jobs.py:122-131` 有 fail_reason、exit_code、message，
没有取消操作者或 interrupted 细分来源；不能编造“被你停止”或唯一的中断原因。
`jobs.py` 枚举顶部对 interrupted 的早期注释已落后于当前恢复实现，上表以实际调用路径为准。

## 新文案

| 状态 | 标题 | 原因 / 动作 |
|---|---|---|
| cancelled（灰） | 已主动停止 | 收到停止指令，未成功完成；未记录操作者。可以重新运行。 |
| orphaned（红） | 执行设备失去连接 | 任务可能仍运行。检查连接并等待同步，避免重复提交。 |
| interrupted（红） | 运行中断（未恢复） | 重连后未认领任务，或失联超时；未记录具体原因。确认设备和残留进程后重跑。 |
| failed（红） | 运行失败 | 将 algo_error / user_error / system_error / oom 翻译为算法退出、输入参数、系统环境、内存显存类别；已有具体文字原样保留。查看日志处理后重试。 |

失败详情目前由卡片已有的 `runtime.fail_reason` 提供（`nodeRuntime.ts:215`、`AlgorithmNode.tsx:588`）；
本次不新增详情 API 请求，也不把未知原因编造成代码报错。完整退出信息仍在任务详情/日志中。

## 改动

- `src/canvas/staleness.ts`：补全状态分支，分离配置证据。
- `src/canvas/nodeStatus.ts`：原因和动作、失败类别翻译、停止优先级、避免重复运行结论。
- 两个对应单测文件：覆盖中断/失联/停止/失败/未知/完成/运行，与真实陈旧计算组合验证。
- `e2e/fixtures/node-status.tsx`：终态卡片使用真实 computeStaleness，复现原问题的数据组合。
- `e2e/node-status.spec.ts`：验证四种标题、无“尚未运行”矛盾，同时保留几何/键盘/缩放检查。

## 验证与截图

前端单测 500 项通过。状态相关浏览器包括实时进度、参数漂移和浅/深状态 tooltip。
首轮深色 hover 超时；最终代码固定后重跑浅/深状态测试，2 项通过；实时进度与参数漂移 5 项通过。未改变 TooltipLayer。
卡片高度、连线锚点、键盘访问、50%/100%/150% 缩放保持通过。
`npm run build` 成功，vendor 和 dist 已重打，仅有既有 bundle 大小提示。
四类状态的浅/深截图已逐张查看，文字可读，状态与动作不再冲突。
截图使用测试卡片，不是对线上任务执行停止或制造故障。

| 状态 | 浅色 | 深色 |
|---|---|---|
| 停止 | [截图](/tmp/node-status-reasons/cancelled-light.png) | [截图](/tmp/node-status-reasons/cancelled-dark.png) |
| 失联 | [截图](/tmp/node-status-reasons/orphaned-light.png) | [截图](/tmp/node-status-reasons/orphaned-dark.png) |
| 中断 | [截图](/tmp/node-status-reasons/interrupted-light.png) | [截图](/tmp/node-status-reasons/interrupted-dark.png) |
| 失败 | [截图](/tmp/node-status-reasons/failed-light.png) | [截图](/tmp/node-status-reasons/failed-dark.png) |

未修改后端状态机、pack 或其它会话的变更；不 push、不重启服务。
