# Pack Manager 前端迁移验收

## 改动与信息去向

- `src/App.tsx:209`：沿用 pathname / pushPath 添加 `/packs` 与 `/packs/`，不引入路由库。Gallery、Library 和画布顶栏都有 Pack Manager 入口；离开未保存草稿会确认。
- `src/App.tsx:2170`、`src/packs/useRecipeChecks.ts:6`：画布继续执行配方和能力检查；请求路径、请求体及后端数据保持原约定。`src/App.tsx:2710` 不再挂载 RecipeProblems / EnvironmentChecks，移除整个检查浮层。
- `src/canvas/NodeCardHeader.tsx`、`AlgorithmNode.tsx`、`NodeInspector.tsx`：移除节点卡的环境与发布状态、源码按钮和 Inspector 的包路径行。`CodeIdentityPanel` 未改动，保留在 Inspector。
- `src/packs/PackManager.tsx:42`：聚合当前包目录、安装记录、计算节点、已保存工作流；读取失败明确提示清单不完整。读取工作流并发限制为 4。
- `src/packs/packManagerModel.ts:17`：规范包名后按包名和版本聚合，包含仅被工作流引用的缺失包与注册表未安装版本；历史离线记录不冒充当前安装。
- `src/packs/PackManager.tsx:120`：注册表目录、搜索、检查目标、问题过滤和 scope 分组。
- `src/packs/PackManager.tsx:155`：每个包显示来源、安装位置、yank；可通过现有 pack-status 查询当前发布状态。
- `src/packs/PackManager.tsx:166`：环境结构化展示要求、当前能力、处理方式。未声明 requires 是信息缺失，不计为错误；缺失和无法确认分别表达。
- `src/packs/PackManager.tsx:170`：配方检查归入“使用情况与配方检查”，逐个列出工作流和节点、变量绑定/值及节点环境，可打开工作流处理。选择此位置是因为它能直接回答修改包影响谁，也满足此次明确指定的信息归宿；未另建运行前检查页面。
- `src/packs/PackManager.tsx:181`：安装命令和源码入口；沿用来源路径打开/复制能力。复制命令明确提示尚未执行安装。yanked、开发版本和内置包不提供发行版安装命令。
- `src/canvas/WorkflowToolbar.tsx:81`、`workflowToolbar.css:2`：窄屏顶栏横向滚动，避免撑宽整个画布。

画布现在保留节点、连线、运行状态、编辑控件与 Inspector；包健康状态、环境清单、配方问题、发布状态及源码入口已移出。用于创建节点的原 Packs 选择器仍保留（包括包名/版本），不是包管理面板；因此不声称画布 DOM 内没有任何包名。节点级代码身份按要求保留。

## 截图

下列场景使用真实 App 路由和组件、受控 API 数据，可复现健康/缺失/空状态。真实实例截图另列；未修改真实工作流、未执行真实运行或安装。

| 场景 | 浅色 | 深色 | 窄屏浅色 | 窄屏深色 |
|---|---|---|---|---|
| 干净画布 | [图](screenshots/canvas-light.png) | [图](screenshots/canvas-dark.png) | [图](screenshots/canvas-light-narrow.png) | [图](screenshots/canvas-dark-narrow.png) |
| 有包、有缺失 | [图](screenshots/manager-missing-light.png) | [图](screenshots/manager-missing-dark.png) | [图](screenshots/manager-missing-light-narrow.png) | [图](screenshots/manager-missing-dark-narrow.png) |
| 有包、无缺失 | [图](screenshots/manager-healthy-light.png) | [图](screenshots/manager-healthy-dark.png) | [图](screenshots/manager-healthy-light-narrow.png) | [图](screenshots/manager-healthy-dark-narrow.png) |
| 无包 | [图](screenshots/manager-empty-light.png) | [图](screenshots/manager-empty-dark.png) | [图](screenshots/manager-empty-light-narrow.png) | [图](screenshots/manager-empty-dark-narrow.png) |
| 真实实例，105 个包版本、18 项待处理 | [图](screenshots/live-manager-light.png) | [图](screenshots/live-manager-dark.png) | — | — |

前次迁移前的画布截图保留在 [上一轮报告](../product-polish/README.md)。

## 验证

- `npm run build`：通过；保留既有大 chunk 提示。
- `npm test`：49 个文件、654 项通过。
- 专项组合回归：19 项通过（包管理、来源打开/复制、产品打磨、F 键、VRAM 透镜、主机进程控制、紧凑 Jobs）。
- 最新 Pack Manager 专项：10 项通过。结果见 [targeted.log](targeted.log)，包含双主题/窄屏、注册表、yank、命令复制、路由返回、部分数据失败与内部错误隐藏。
- 迁移后的来源/引用/按钮/包身份组合：12 项通过，2 项既有 footer spacing 失败（见 [migrated.log](migrated.log)）。
- 配方/能力请求体断言、画布节点/连线、参数编辑、保存、运行请求、Jobs/Runs/Inspector 均有浏览器回归覆盖。源码迁移沿用跨机器路径和 Unicode 长路径用例。
- 完整 Playwright：**120 通过 / 25 失败 / 1 跳过**，未达到全过。25 项均在上一轮失败集合中，见 [失败对照](failure-comparison.md)；最终结果见 [e2e-final.log](e2e-final.log)。不可将专项通过替代全量通过。
- 不重启已有 hololab 服务；使用独立 Vite 预览；未改后端接口或后端文件，未改 website，未 push。

## 边界和遗留

1. **浏览器直接安装尚未实现**：现有服务没有 HTTP 安装或注册表列表端点，安装能力只有 `hololab pack install` CLI。在“只改前端”的约束内提供精确、带 shell 引号保护的命令，并允许读取浏览器可访问的静态注册表。要实现一键安装需后端提供受控安装接口。没有把复制命令伪装成安装成功。
2. 注册表 HTTP(S) 目录需允许浏览器访问/CORS；file:// 注册表走 CLI；发布状态未查询时显示未知，不从历史安装记录推断未 yank。
3. 使用关系以当前已保存草稿为准，不包含未保存编辑或历史快照。大量工作流的配方检查会增加首次汇总时间；检查并发已限制，但尚未做后端批量索引（本任务禁止后端改动）。
4. 原节点创建 Packs 选择器保留。若“任何包相关”也包括创建选择器本身，需要将创建操作统一迁至已有右键菜单后删除该栏；此次未擅自移除节点创建入口。
