# 视频背景切换：真实页面诊断

2026-10-17，直接访问 `http://127.0.0.1:8828`，打开 `MiddleCutPro` 工作流（`0eb1b412-e095-4f9d-b55a-5862cf9fc705`）的 `single-video-source.videos_dir`，在 6 秒处暂停。未 mock API、视频、poster 或组件。

## 结论

目标左右黑边已经编码进服务端生成的代理视频，不是 `<video>` 的 CSS 留白。CSS 背景切换实际生效，但被不透明的视频像素盖住。按要求停止功能修改；本提交仅保存诊断证据，没有更改产品代码。

原视频为 868×1012、H.264/yuv420p；真实网格预览加载 `/_preview/320x180/.../input.mov?fps=15`，为 320×180、H.264/yuv420p。后端 `hololab/node/fileserver.py:652` 的过滤器先 `scale=320:180:force_original_aspect_ratio=decrease`，再 `pad=320:180:(ow-iw)/2:(oh-ih)/2:color=black`，最后编码为没有 alpha 通道的 H.264/yuv420p。约 154×180 的竖屏图像被填充到 320×180。原视频自身另有黑色场景背景，两者不能混为 CSS 留白。

## DOM 实测

浏览器视口 1500×1000。初次加载画布缩放为 2，video 为 608×340.25。为了完整显示节点并与用户提供的 DevTools 尺寸对照，仅调整诊断浏览器 DOM 的画布 transform 到 1.229440789 倍，并把节点移到可见位置；没有保存工作流或改动视频样式/布局。调整后测得 **373.75×209.16**，与用户提供的尺寸一致。以下坐标均为该诊断视口的 CSS 像素。

| 层 | tag / class 或标记 | x, y | 宽×高 | background-color（黑色态） | padding | border | object-fit |
|---|---|---|---|---|---|---|---|
| 卡片 | div.hololab-node | 346, 92 | 418.01×481.96 | rgb(250,250,250) | 0 | 1px solid rgb(189,189,189) | — |
| 抽屉 | div.nodrag | 347.23, 234.48 | 415.55×338.25 | rgb(23,32,51) | 8px | 顶部边框 | — |
| 预览容器 | div.hl-video-preview | 357.06, 282.43 | 395.88×280.47 | rgb(23,32,51) | 8px | 0 | — |
| 画面区 wrapper | div | 366.90, 292.26 | 376.21×211.62 | rgba(0,0,0,0) | 0 | 0 | — |
| 网格 | div[data-hl-video-grid] | 366.90, 292.26 | 376.21×211.62 | rgba(0,0,0,0) | 0 | 0 | — |
| tile | div[data-hl-tile] | 366.90, 292.26 | 376.21×211.62 | rgb(0,0,0) | 0 | 1px solid rgb(217,217,217) | — |
| video | video | 368.13, 293.49 | 373.75×209.16 | rgb(0,0,0) | 0 | 0 | cover |

`videoWidth/videoHeight = 320/180`，`object-position = 50% 50%`。`cover` 将不透明的 320×180 代理帧铺满 video 盒子，不产生 CSS letterbox。所有上述元素的 `::before` 和 `::after` content 均为 `none`；播放器 readyState=4、currentTime=6，截图取的是已解码播放帧，不是 poster。

## 三态数值和像素

三态中所有祖先层背景均未改变。右黑区采样取卡片截图坐标 `(350,300)`，对应视口约 `(696,392)`，明确位于 video 边界 `[368.13,741.88] × [293.49,502.65]` 内。左黑区采样 `(40,300)`；区域 1 取 `(10,300)`。

| 状态 | video background-color | video background-image | 左/右黑区实际 RGB | 区域 1 实际 RGB |
|---|---|---|---|---|
| 黑色 | rgb(0,0,0) | none | (0,0,0) | (23,32,51) |
| 透明 | rgba(0,0,0,0) | none | (0,0,0) | (23,32,51) |
| 棋盘格 | rgba(0,0,0,0) | repeating-conic-gradient，#d8d8d8/#b0b0b0 | (0,0,0) | (23,32,51) |
| 临时直接覆写 video 为 magenta | rgb(255,0,255) | none | (0,0,0) | (23,32,51) |

[黑色截图](real-black.png) · [透明截图](real-transparent.png) · [棋盘格截图](real-checkerboard.png) · [强制洋红探针](real-magenta-probe.png)

通过 `canvas.drawImage(video)` 直接读取已解码像素（canvas 不包含 DOM/CSS 背景），得到 [320×180 代理帧](proxy-frame.png)。其 x=0、40、79、280、319 列全列最大 RGB 值都是 0。点击真实预览放大后读取原文件，得到 [868×1012 原视频帧](original-frame.png)。这独立证明黑边来自代理媒体像素，而非更高优先级 CSS、伪元素或浏览器默认背景。

完整证据：[逐层 DOM / 计算样式 / 三态](dom.json)、[截图像素采样](pixels.json)、[原视频 ffprobe](source-metadata.json)、[代理视频 ffprobe](proxy-metadata.json)。

## 为什么前两次验证没发现

先前 E2E 将生成的竖屏视频直接用于模拟的媒体地址，并进入使用原视频的 ZoomOverlay（contain）。它没有经过真实 `/_preview` 和 `/_thumb` 转码链路，因此看到的是可修改的 CSS 留白。用户实际看到的是 VideoTile（cover）中的已填黑代理视频。这两种留白来源不同。

## 后续可选方案（本次不实施）

1. 保留轻量代理：代理/缩略图保留原宽高比，不编码黑色 padding；前端在固定 video 框中用 contain，才会产生可切换背景的 CSS 留白。这需要调整服务端媒体生成，并处理旧代理缓存，超出单纯背景 CSS 修复。
2. 前端改用原视频 + contain：绕过填黑代理，能切换视频矩形外的留白，但增加原视频传输与解码成本。
3. 若只想放大主体，可提供显式裁剪/缩放选项。单独给当前 16:9 代理设置 cover 不会去掉其中的编码黑边（当前已经是 cover）；需要知道真实内容矩形。裁剪也不会去掉原画面里的黑色场景背景，且可能损失内容。

不能简单把黑色像素都设为透明：这会影响原视频中真实的黑色部分。

## 验证

真实页面 Playwright 完成 DOM dump、三态切换、6 秒视频帧读取、直接样式覆写和像素采样；ffprobe 确认媒体格式。`npm run build` 通过（仅有打包体积 warning）。
