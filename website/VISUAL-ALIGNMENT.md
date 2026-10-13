# HoloLab 官网视觉对齐记录

本次保留首页分区、文档三栏、路由和内容。参考产品源码，并在独立浏览器上下文只读访问线上产品 Gallery，分别采集浅、深主题截图和计算样式。未打开可编辑工作流，未点击运行、新建、删除等操作；采集脚本阻断非 GET/HEAD/OPTIONS HTTP 请求。

## 产品实际视觉语言

以下源码路径相对于 HoloLab 仓库根目录。产品样式优先参考实际组件与覆盖规则，而不只参考 token 名称。

### 字体与密度

来源：`hololab/frontend/src/theme/tokens.css:193`、`hololab/frontend/src/styles.css:22`。

- 无衬线：`"Inter", "SF Pro Text", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`。
- 等宽：`"SF Mono", "JetBrains Mono", ui-monospace, "SFMono-Regular", Menlo, Monaco, Consolas, "Liberation Mono", monospace`。
- 字号：10px 微型信息、11px 辅助信息、13px 控件/面板标题、15px 卡片标题、18px 大标题、24px 最大基础级别。
- 字重：400 / 500 / 600 / 700；没有 650 这一级。
- 行高：紧凑 1.2、普通 1.45、控件 1.3。线上 Gallery body 实测 13px / 18.85px。
- 产品与站点原先已使用同一字体栈。本次不额外加载字体；以相同浏览器环境中的实际字形来源进行对照，避免把“声明 Inter”误报为“实际下载并使用 Inter”。
- 本机 Chromium 实测两边的 HoloLab 字标均回退到 Liberation Sans（非自定义字体），浅、深主题一致。

### 双主题色值

来源：`hololab/frontend/src/theme/tokens.css:20`（浅色）、`:102`（深色）。

| 用途 | 浅色 | 深色 |
| --- | --- | --- |
| 页面背景 `bg` | `#f6f6f6` | `#181818` |
| 面板 / 卡片 `surface` | `#ffffff` | `#1f1f1f` |
| 次级表面 `surface-2` | `#fafafa` | `#252526` |
| 内嵌表面 `surface-alt` | `#f0f0f0` | `#252526` |
| hover 表面 | `#e9e9e9` | `#2d2d2d` |
| 画布 | `#ededed` | `#181818` |
| 普通边框 | `#d9d9d9` | `#3c3c3c` |
| 强边框 | `#bdbdbd` | `#5a5a5a` |
| 细边框 | `#e8e8e8` | `#2b2b2b` |
| 标题文字 | `#171717` | `#cccccc` |
| 正文文字 | `#262626` | `#cccccc` |
| 次要文字 | `#5e5e5e` | `#9d9d9d` |
| 更弱文字 | `#767676` | `#6e6e6e` |
| 强调色 / 主按钮 | `#0078d4` | `#0078d4` |
| 主按钮 hover | `#006abc` | `#026ec1` |
| 主按钮文字 | `#ffffff` | `#ffffff` |
| success | `#16825d` | `#89d185` |
| warning | `#946c00` | `#cca700` |
| error | `#c42b1c` | `#f14c4c` |
| info | `#0078d4` | `#3794ff` |
| neutral | `#64748b` | `#8b8b8b` |

站点 token 快照与产品 token 文件逐字一致；没有重新调一套近似色。状态色只供状态语义使用，没有为了配色而给官网内容添加虚构状态。

### 几何与层级

来源：`tokens.css:175`、`:186`、`:221`，以及 `styles.css:34`。

- 间距以 4px 为基准：4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48。
- 圆角：4px 控件、6px 卡片，另有 10 / 12 / 999px，不能把所有容器都当卡片。
- 边框通常 1px。普通面板直角；卡片圆角；没有浮起的彩色卡片阴影。
- 控件高度 24 / 28 / 32px；图标 14px；面板标题栏 32px。
- 卡片实际 `box-shadow: none`，即使存在 shadow token 也不套给卡片。
- 顶栏最低 48px；线上 Gallery 实测 52px，表面色加 1px 底边和 `0 1px 0 var(--border-subtle)`。
- 小阴影：浅色 `0 1px 2px rgba(15,23,42,.06), 0 1px 1px rgba(15,23,42,.04)`；深色 `0 1px 2px rgba(0,0,0,.4)`。
- 浮层阴影：浅色 `0 12px 30px -12px rgba(15,23,42,.22), 0 4px 10px -4px rgba(15,23,42,.10)`；深色 `0 18px 40px -14px rgba(0,0,0,.65), 0 4px 10px -4px rgba(0,0,0,.40)`。
- 焦点环：浅色 `0 0 0 3px rgba(37,99,235,.28)`；深色 `0 0 0 2px rgba(0,120,212,.72)`。

### 组件

| 组件 | 产品规则 | 源码 |
| --- | --- | --- |
| 主按钮 | 13px/600，蓝底白字，28px 常规高度，4px 圆角，左右 12px 内边距 | `src/ui/controlStyles.ts:26` |
| 次按钮 | 13px/500，surface 底、strong 边框，hover 改表面色 | `src/ui/controlStyles.ts:4`、`src/styles.css:64` |
| ghost | 透明底和边框，muted 字，hover 使用 surface-hover | `src/styles.css:76` |
| 输入 / select | 28px 高，4px 圆角，strong 边框，左右 8px，焦点环；select 自绘小箭头 | `src/styles.css:161`、`:197` |
| 标签 / 徽章 | 非交互计数 24px 高、11px 字、透明底，无边框和圆角胶囊 | `src/ui/controlStyles.ts:34` |
| 卡片 | surface 底、1px border、6px 圆角、16px 留白，无阴影 | `src/Gallery.tsx:394` |
| 面板标题 | 32px 高，10px/600，`.08em` 字距，底部分隔线 | `src/styles.css:57` |
| tooltip | 黑底白字、4px 圆角、4×8px 留白、11px 字、小尖角、浮层阴影；标题型另用 surface-2 | `src/styles.css:84` |
| 代码块 | surface-2、细边框、4px 圆角、等宽字体、11px / 1.45 | `src/ui/Markdown.tsx:224`、`src/styles.css:580` |
| 另一 Markdown 查看器 | 使用 surface-alt、同样细边框/4px 圆角，11px / 1.5 | `src/ui/MarkdownView.tsx:193` |
| 链接 | accent 颜色，hover 下划线 | `src/styles.css:216` |
| 字标 | 文本 HoloLab，13px/600，字距 -.01em | `src/Gallery.tsx:180` |

本表缩写 `src/` 均指 `hololab/frontend/src/`。

## 逐项差异与最终决策

| 项目 | 改前站点 | 本次处理 |
| --- | --- | --- |
| 字体栈与基本颜色 | 已与产品一致 | 保留并用线上计算样式核对，避免无效换字体 |
| 字标 | 20px/650 + 自制 H 图形 | 改为产品文字字标 13px/600；站点图标文件未扩展成新标识系统 |
| 顶栏 | 72px 内层、宽间距、14px 下划线导航 | 52px 整体高度；13px、28px 高工具栏式导航；非选中项采用 ghost 反馈 |
| 按钮 | 约 43px 高、14px、3px 圆角 | 顶栏 28px；首页主入口采用产品已有的 32px 大控件；13px、4px 圆角、500/600 字重 |
| 主题选择 | 无边框原生 select + 文字符号 | 产品输入框材质与小箭头，14px 主题图标，产品式 tooltip；保留三选项操作方式 |
| 侧栏 | 继承页面背景，标题较大 | 独立 surface 面板，1px 分隔边框，32px 微型标题栏，13px 导航项 |
| 代码块 | surface 底、普通边框、3px 圆角、1.8 行高 | surface-2、subtle 边框、4px 圆角；保留阅读用 13px / 1.6，移动端 12px |
| 行内代码 | 无边框、2px 圆角 | surface-alt、细边框、4px 圆角，等宽数字 |
| 首页示意节点 | 3px 圆角、strong 边框、蓝色连线 | 产品卡片 6px 圆角、普通边框；32px 节点标题栏、20px 画布网格、普通 edge 色 |
| 文档入口 | 纯分隔线列表 | 保持原列表顺序，使用产品 6px 无阴影卡片与 hover 边框 |
| 焦点与滚动条 | 通用轮廓 / 默认滚动条 | 控件使用产品双主题 focus-ring 和滚动条色 |
| 间距 | 多处随意的 17/23/25/66px | 控件、面板与主要分区采用产品 4px 基准节奏 |
| 首页标题 | 最大 54px/650、负字距较大 | 最大 40px/600、-.01em；仍保留官网标题层级 |
| 阅读正文 | 15px / 1.8、三栏、最大约 780px 正文 | 保留；不把长篇教程缩成产品的 13px / 1.45 密集面板 |
| 文档标题 | 34px | 28px，移动端 24px；维持文章结构 |

## 验证与对照材料

本次结果：`npm run build` 通过；13 项同步测试通过；浏览器检查通过（13 个文档路由的浅/深主题、链接与锚点、404、历史导航、主题持久化/跟随系统、移动端导航与溢出）；线上产品只读视觉对照通过。

常规回归仍使用 `npm test` 和 `npm run test:browser`。新增可选的只读视觉对照命令：

```bash
# 先启动 npm run preview（默认本机 5200）
PRODUCT_URL=http://<product-host>:8828 npm run test:visual
```

`tests/visual.mjs` 在同一 Chromium 版本、1440×1000 视口中核对：

- 产品与官网实际字体栈、实际渲染字标字体、文字/背景色；
- 顶栏整体高度和表面色；主按钮字体、颜色、圆角、边框、留白；
- 侧栏底色、代码块表面/圆角、键盘焦点和 tooltip；
- 官网主入口 32px 与产品常规 28px 是记录在案的尺寸选择，不误判为完全相同。

截图与计算样式在忽略的 `test-results/visual/` 下：

- `product-light.png` / `product-dark.png`：原始产品 Gallery。
- `home-light.png` / `home-dark.png`、`docs-light.png` / `docs-dark.png`：同尺寸站点视口。
- `compare-home-{light,dark}.png`、`compare-docs-{light,dark}.png`：左产品、右站点，原尺寸并排。
- 同名 `.html` 可打开查看；`metrics.json` 为实际计算样式和字体记录。

对照板是浏览器渲染的报告页面，嵌入原始截图，没有修改产品截图中的界面。产品截图包含现有项目名称，仅作本地验收材料，不进 Git 或站点发布目录。

本次实看的是产品 Gallery；画布、输入、代码和 tooltip 的细部同时参考源码，不声称在在线工作流中逐项操作过。字体在其他操作系统可能回退到不同字形，但站点与产品使用同一套回退顺序。
