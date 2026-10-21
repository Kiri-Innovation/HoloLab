# HoloLab Meniscus 定稿与集成

用户选定：02 液面瓶 × KIRI 家族青 **#30E3E3**。本目录为独立、可复现的定稿交付，不依赖上级探索页或其他方向的文件。

## 定稿打磨

- 保留 64×64 画布、半径 16 的圆角青色容器和白色几何瓶，无描边、渐变或文字。
- 保留短直颈、向下扩张的斜肩与闭合宽底；肩颈增加短二次曲线，平滑转折，瓶底两侧采用对称二次曲线过渡。
- 原横向液面切口改为浅弧负形：上沿中心下沉 1 个源坐标单位，下沿保持约 4 单位间距（16px 时约 1px），避免微尺寸闭合。
- 精确使用最新确认的 #30E3E3，覆盖探索版 #00E3E3。

## 资产

`assets/hololab-icon.svg` 为主源输出，`generate.cjs` 内保存手写几何路径；`assets/favicon.svg` 为同形 favicon。

- favicon：SVG + 16/32/48 PNG；另提供 `favicon-light.*` 与 `favicon-dark.*` 两套文件。
- 浅深两套有意保持相同青底白瓶，透明圆角外部适配载体，不按主题改变品牌色。产品实际挂载统一的默认 SVG 与 PNG，避免主题分歧。
- 应用图标：`app-icon-192.png`、`app-icon-512.png`，均保留圆角容器。这些不是 maskable 图标。
- 产品与官网均无 PWA web manifest / service worker 挂载；`website/public/images/docs/manifest.json` 是文档截图清单，不是 PWA 配置，因此未改。应用图标提供备用，未额外引入 PWA。

## 集成清单（仓库根目录相对路径）

| 位置 | 文件 | 改动 |
| --- | --- | --- |
| 产品 favicon | `hololab/frontend/index.html` | 原无 icon 链接；增加 16/32/48 PNG 回退和 SVG 主图 |
| 产品资产 | `hololab/frontend/public/{hololab-icon.svg,favicon*,app-icon-*}` | 新增定稿资产 |
| 流程列表品牌位 | `hololab/frontend/src/Gallery.tsx` | 在原 HoloLab 文字前加装饰性 20px 图标 |
| 工作区品牌位 | `hololab/frontend/src/canvas/WorkflowToolbar.tsx` | 仅在原返回按钮内加图标，未改按钮行为或画布功能 |
| 素材页品牌位 | `hololab/frontend/src/Artifacts.tsx` | 原品牌按钮内加图标 |
| Library 品牌位 | `hololab/frontend/src/library/Library.tsx` | 原品牌文字前加图标 |
| 官网 favicon | `website/index.html` | 保留 `/favicon.svg` 挂载，增加 PNG 回退 |
| 官网资产 | `website/public/{hololab-icon.svg,favicon*,app-icon-*}` | 替换旧 favicon，补充全套资产 |
| 官网页头 | `website/src/main.tsx` 的 Header | 原品牌链接内加图标 |
| 定稿交付 | `website/design-exploration/hololab-icon/final/` | 主源、导出、预览、截图与验证记录 |

名字、文案与既有容器布局未修改；仅图标占位与间距增加。未改内容页、packs、Inspector 或画布功能逻辑。

## 验收

- `hololab/frontend`: `npm run build` 通过（含既有 vendor build）。
- `website`: `npm run build` 通过（含既有 prebuild 文档同步与 postbuild 本地化流程）；无内容源文件变化。
- 两边 Vite 均提示现有大 bundle >500kB 警告，不阻断构建。
- 产品截图来自既有 `http://127.0.0.1:8828` 服务，构建后直接加载新资源，**未重启服务**。
- 官网截图来自构建产物 `website/dist` 的本地 5200 端口静态预览，未部署外网。
- `screenshots/product-{light,dark}-browser.png` 与 `website-{light,dark}-browser.png` 为 Xvfb 内真实 Chromium 窗口截图，包含真实标签栏与 favicon，并非 HTML 模拟。
- `screenshots/final-16-{light,dark}.png`：DPR=1、16px SVG/PNG 与 32/48px 对照；右侧为 16px PNG 最近邻放大 8 倍。
- 人工检查：短直颈、斜肩、宽底保持可辨；液面间隔仍存在，16px 弧度趋于平直，32px 以上更明确。未进行外部用户辨识实验。
- 两边浅深主题下图标成功加载，所有挂载 favicon 返回成功；产品素材页、Library 与工作区品牌图标可见。官网 390px 两主题均无横向溢出。记录见 `verification.json`。

## 复现

从仓库根目录运行：

```sh
node website/design-exploration/hololab-icon/final/generate.cjs
```

此命令重新生成并复制图标到两边 public，随后需分别 build。打开 `index.html` 可离线查看定稿与微尺寸板。

截图脚本 `verify.cjs` 需要现有产品服务 :8828、官网构建预览 :5200、Xvfb :97，以及 Pillow 的 XCB 支持。它只读取页面、在临时浏览器 profile 中设置主题，不重启服务、不修改工作流。

无待确认的图标位置；没有 PWA manifest 可更新。未 push。
