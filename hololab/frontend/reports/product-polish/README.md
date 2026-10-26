# HoloLab 前端产品打磨验收记录

范围：仅 `hololab/frontend/`。没有修改后端 Python、网站、校验规则或生产数据，没有重启现有 hololab 服务。

## 截图与复现

截图由 Chromium 实际渲染 React 产品组件生成，使用固定证据/能力/变量数据，不是设计稿。相同 fixture 在修改前 HEAD 副本（4174）及修改后前端（5173）渲染；`NodeInspector` 使用真实组件。浅/深主题各覆盖 1280px 和 390px。`capture.mjs` 保存整页、逐组件和展开滚动后的证据截图。

启动独立前端 `npm run dev -- --host 127.0.0.1`，打开 `/reports/product-polish/index.html`；运行 `node reports/product-polish/capture.mjs after`。组件 fixture 不修改真实工作流。完整 App 交互测试使用拦截 API，真实导入/文件回归使用独立临时数据库和 18764 端口。

| 组件 / 宽度 | 浅色 | 深色 |
| --- | --- | --- |
| 配方操作 / 1280px | [改前](before/recipe-light-1280.png) · [改后](after/recipe-light-1280.png) | [改前](before/recipe-dark-1280.png) · [改后](after/recipe-dark-1280.png) |
| 配方操作 / 390px | [改前](before/recipe-light-390.png) · [改后](after/recipe-light-390.png) | [改前](before/recipe-dark-390.png) · [改后](after/recipe-dark-390.png) |
| 变量与 Inspector 参数绑定 / 1280px | [改前](before/variables-light-1280.png) · [改后](after/variables-light-1280.png) | [改前](before/variables-dark-1280.png) · [改后](after/variables-dark-1280.png) |
| 变量与 Inspector 参数绑定 / 390px | [改前](before/variables-light-390.png) · [改后](after/variables-light-390.png) | [改前](before/variables-dark-390.png) · [改后](after/variables-dark-390.png) |
| 本地能力检查 / 1280px | [改前](before/environment-light-1280.png) · [改后](after/environment-light-1280.png) | [改前](before/environment-dark-1280.png) · [改后](after/environment-dark-1280.png) |
| 本地能力检查 / 390px | [改前](before/environment-light-390.png) · [改后](after/environment-light-390.png) | [改前](before/environment-dark-390.png) · [改后](after/environment-dark-390.png) |
| 代码身份 / 1280px | [改前](before/identity-light-1280.png) · [改后](after/identity-light-1280.png) | [改前](before/identity-dark-1280.png) · [改后](after/identity-dark-1280.png) |
| 代码身份 / 390px | [改前](before/identity-light-390.png) · [改后](after/identity-light-390.png) | [改前](before/identity-dark-390.png) · [改后](after/identity-dark-390.png) |
| 历史快照 / 1280px | [改前](before/snapshot-light-1280.png) · [改后](after/snapshot-light-1280.png) | [改前](before/snapshot-dark-1280.png) · [改后](after/snapshot-dark-1280.png) |
| 历史快照 / 390px | [改前](before/snapshot-light-390.png) · [改后](after/snapshot-light-390.png) | [改前](before/snapshot-dark-390.png) · [改后](after/snapshot-dark-390.png) |

完整视图：[改前浅](before/light-1280.png) · [改后浅](after/light-1280.png) · [改前深](before/dark-1280.png) · [改后深](after/dark-1280.png)。

证据展开：[代码身份浅色](after/identity-expanded-light-1280.png) · [代码身份深色](after/identity-expanded-dark-1280.png) · [能力检查窄屏](after/environment-expanded-light-390.png) · [历史未记录说明](after/snapshot-expanded-dark-390.png)。面板保留正常滚动限制，截图包含滚动后的视图。

实际工作流页面：[浅色](after/workflow-light.png) · [深色](after/workflow-dark.png)。截图固定过渡动画到最终状态，避免将主题切换中间帧当作完成状态。

## 改动

- `src/canvas/RecipeFileActions.tsx:4`：统一按钮、忙碌/成功/失败反馈；工具栏收拢到配方文件菜单；保持导出 Blob、导入文件文本原样传递；重载确认；隐藏内部响应错误。
- `src/canvas/RecipeProblems.tsx:6`、`src/App.tsx:2704`：沿用已有诊断，问题节点可定位到画布与 Inspector；配方与能力检查顺序排布，避免覆盖；异常提示用产品文案。
- `src/canvas/WorkflowVariables.tsx:5`：紧凑标签/控件行，类型、说明、共享默认值、本地值、回退默认值；添加、改名、删除；改名同步参数绑定（`src/App.tsx:2983`、`:3084`），类型变更保留原值交由原解析器校验；删除及类型变更确认。
- `src/canvas/PackParameterField.tsx:73`：绑定下拉满宽对齐，缺失或类型不匹配的变量明确提示，暂无同类型声明时指引添加；保留原内联参数值与解析说明。NodeInspector 通过复用该组件获得更新，没有重写参数规则。
- `src/canvas/EnvironmentChecks.tsx:57`：缺失、无法确认、未声明分开计数；每项按要求/当前/处理方式显示；保留能力边界说明与原始请求；空结果不崩溃。
- `src/canvas/CodeIdentityPanel.tsx:39`、`src/canvas/EvidenceValue.tsx:4`：热/冷、Manifest、忽略规模、Git、未覆盖/未声明来源、冻结参数分组；长值按需展开；完整原始记录仅在 `?debug=1` 下显示；旧运行不补写代码身份。
- `src/canvas/SnapshotBanner.tsx:28`：中文只读提示、可换行布局、黑底主操作、结构化冻结变量；恢复确认保持；准备/恢复失败不暴露内部错误。
- `src/canvas/workflowEvidence.css:1`：复用既有主题色、圆角、间距、字体与控件尺寸 token；主操作深黑底浅字，删除使用主题浅灰表面，无胶囊。
- `src/App.tsx:1150`：工作流异步加载读取最新包目录，避免配方先发起读取、目录随后完成时仍留下没有参数的占位包。只影响前端呈现，不变更包识别规则。

## 回归

- `npm run build`：通过。保留既有 vendor 分包体积提醒，没有编译错误。[日志](build.log)
- `npm test`：48 文件、649 测试通过（包含新增 2 项内部错误/结构化证据检查）。[日志](unit-tests.log)
- 本次浏览器专项：9/9 通过，覆盖导出下载逐字节相同、原始配方上传、导入导航/节点定位、变量改名同步绑定、解析本地值、能力请求投影不变、删除确认、内部错误隐藏、调试开关、快照双主题及原 Inspector 参数行为。[日志](interactions.log)
- `HOLOLAB_RECIPE_TEST_ROOT=/tmp/hololab-recipe-browser-polish node e2e/recipes.mjs`：通过。运行于独立 18764 Gateway 和临时数据库，验证真实导入、缺失包仍保留节点/边、冷打开、文件保存、外部编辑冲突、确认后重载与下载。生产 8828 服务未重启。[日志](live-recipes.log)
- 仅读运行现有 Python 回归（没有修改 Python）：变量、配方文件、绑定、能力共 21 项通过，其中包括变量影响分析/派发/冻结快照，以及删除声明阻止执行。[日志](backend-regression.log)
- 布局检查：浅/深 × 1280/390，页面及所有受测面板的 scrollWidth 均不超出 clientWidth。[测量](after/layout-audit.json)
- **完整 `npm run e2e` 未全绿：99 通过 / 35 失败 / 1 跳过。** HEAD 对照为 64 通过 / 68 失败 / 1 跳过；当前 35 个失败用例都在改前 HEAD 独立副本中也失败；不是将失败测试跳过或改成通过。当前套件仍包含依赖自动保存的旧断言、节点/tooltip 几何断言等。没有恢复自动保存或变更执行规则来迎合测试。[当前完整日志](e2e-final.log) · [HEAD 对照日志](baseline-e2e.log) · [逐项对照](e2e-comparison.md)
- 完整 E2E 后补充的菜单 Esc/外部点击、基线确认、调试开关与文案收尾，由上述 9 项专项和最终 build / 649 单测复验；未再次重复整个已知失败套件。


## 遗留

- 用户要求的“现有测试全过”尚未满足：完整浏览器套件的 35 项既有失败保留在报告，需单独同步旧测试与当前产品行为，并调查剩余几何/交互失败。

- 整体应用仍有本任务之外的蓝色 Run 等既有按钮、节点头部较长的环境/发布状态挤压标题；本次只统一新增配方、变量、快照、证据操作，未全局重绘按钮和节点头。
- 原生确认框沿用项目行为；未引入新的全局模态框系统。
- 没有启动真实 GPU 训练；变量影响执行的依据来自现有 resolver/dispatch/冻结快照测试与浏览器请求链路。
