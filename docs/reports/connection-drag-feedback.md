# 拖拽连线实时校验提示

本次只补反馈通道；不改 pack、不调整维度规则、不替用户修正工作流。

## 行为

- 拖拽到端口、鼠标尚未松开时，立即显示就近提示。兼容为绿色 `✓ 可以连接`；不兼容为红色 `✕ 无法连接`，附原因、预期类型与实际类型。
- 锚定鼠标到达的端口；从输入反向拖到输出也适用。
- 拖离端口或松手到空白处，提示消失。失败松手时在原端口停留 4 秒；成功后消失。Escape 隐藏提示，新操作、失焦或 pointercancel 清理提示。
- 普通 tooltip 仍遵守 350ms、按住鼠标时隐藏的规则。只有连线专用通道绕过按下屏蔽。
- 使用已有 TooltipLayer 的节点同色卡片、边框阴影、尖角、缩放与避让。绿色/红色采用 `--success` / `--error`。没有改变端口尺寸或连线锚点。

## 实现

- `src/canvas/connectionValidation.ts`：将 App 原有判定抽为纯函数。继续调用现有 `effectiveOutputType` / `effectivePortArrayed` / `effectivePortDimLabels` / `portsCompatible`；类型字符串只用于最终展示。
- `src/App.tsx`：构造缓存的图类型上下文；拖拽和最终 onConnect 共用 `checkConnection`。仍保留控制台诊断。重复连线也明确提示（原先 addEdge 本身就不添加重复边）。
- `src/canvas/ConnectionFeedback.tsx`：订阅 xyflow 的端口变化，不订阅每帧鼠标位置；按端口 DOM 锚定。`onConnectEnd` 管理松手反馈，记录 onConnect 的原始结果，避免成功新增边后被误判成重复。
- `src/ui/connectionTooltip.ts` / `TooltipLayer.tsx`：独立事件通道，复用定位与 DOM portal；带 `role=status` 通知。
- 不发起连线校验 API 请求；图信息变化或 hover 端口变化时在内存里计算。现有保存/运行 API 行为不变。

## 用户案例

只读提取工作流 `272b31c3-e2ca-4fd5-a8c8-03a38a97711d` 的相关图结构和已安装 manifest，保存为测试 fixture；删去设备、路径和无关执行参数，重新排列测试画布位置。

`merge_g0.folder → stg-train@0.3.0.colmap_frames` 的前端判定：

```text
✕ 无法连接
数组维度的层数或轴名称不匹配。
预期：arrayed[frame]<colmap-folder>
实际：arrayed[group]<colmap-folder>
```

这是实际类型数据在应用界面中的回放测试，不是在在线工作流上创建/删除连线。兼容对照仅在测试 catalog 增加 group 轴输入；真实 STG 声明未修改。

## 验证

- `npm test`：17 个文件、427 项通过，其中新共享判定 67 项（含类型/数组/广播/fan-out 组合及真实案例）。
- 浏览器：63 项既有用例通过；新增浅/深主题 2 项通过复测。新增测试覆盖按住鼠标的红/绿提示、失败松手 4 秒、正常创建连线、空白处取消、反向拖线锚定、Escape 隐藏、普通 tooltip 恢复。
- 50% / 100% / 150% 缩放验证；端口几何保持不变。现有标题、状态、端口、连线 tooltip，以及快照和边选择回归通过。
- `npm run build` 成功，vendor 和 dist 已重打。仅有既有 chunk 大小提示。
- 截图已自己查看：文字可读，红/绿区分明确，尖角指向 hover 端口。

浏览器全套最初新增用例在取消后移到画布外触发自动平移，导致最终 hover 超时；改为画布内空白处取消并恢复测试缩放后，两主题复测通过。生产代码的提示生命周期已用同步结束标记和提交前判定结果保护，避免松手的异步 effect 清掉计时器、或将成功连接误报成重复。

### 截图（鼠标未松开）

- [浅色，不兼容](/tmp/hololab-connection-feedback/light-drag-invalid.png)
- [深色，不兼容](/tmp/hololab-connection-feedback/dark-drag-invalid.png)
- [浅色，兼容](/tmp/hololab-connection-feedback/light-drag-valid.png)
- [深色，兼容](/tmp/hololab-connection-feedback/dark-drag-valid.png)

日志同目录 `/tmp/hololab-connection-feedback/`。本机临时证据不纳入 git。

## 边界

此次只显示当前前端连线判定；不把后端运行期文件契约检查搬到拖拽事件中。上述 group / frame 差异仍需后续单独处理。本次不更改 packs、不更改在线图，不 push、不重启服务。
