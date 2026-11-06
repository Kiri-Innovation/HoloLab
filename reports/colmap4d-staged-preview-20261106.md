# Colmap4D 分级预览与相机组件支持

2026-11-06。本轮未重启任何生产服务、未 push、未修改任何 pack 或 ColmapUtil 子模块。

实现与前端部署完成，但**线上反向隧道验证未通过**；不能把直连文件服务的成功当作线上端到端成功。当前生产页面已引用 `index-D5-ltfON.js`，health 200。后端新增的 tag 默认映射等待未来正常服务更新生效；本轮前端 tag fallback 已支持尚无 preview 声明的 cams handle，无需为此重启。

## 能否只加载相机

可以。ColmapUtil 的 `fileDropHandler.js:762` 入口要求 cameras / images / points3D 三表；其中 points3D 可以是空表。桥接生成 8 字节、count=0 的 points3D.bin，同时提供 cameras、去掉观测的 images、times、time_meta。真实 split 输出 `03fb2084-8f8c-43c2-9bda-aa27955ede38/4d_cams` 已显示 81 个 image poses、1 个 intrinsic camera 和 0–2.667 秒时间轴。

cams 的表直接位于组件根目录，故映射使用 `preview: {viewer: colmap4d, member: "."}`。不请求 points3D、points_t 或像素图片。通过 viewer 既有 `hololab-viewer-camera` 消息接口自动对准相机中心及轨迹范围，避免原固定工厂视角看不到小尺度轨迹。没有修改 viewer 通用默认视角或持久化设置。

## 体积与交互

- 图片：现有 `/api/nodes/{node_id}/paths` 分页 stat API，递归汇总 `images/` 中所有常规文件的 size，包括未被相机引用的文件；不把目录 inode 的 st_size 当总量。缺失路径、符号链接或不可测量条目保守显示“体积未知”，仍须手动选择。实际加载仅请求 images 表引用的像素文件，4 路并发。
- 点云：现有文件代理的 `Range: bytes=0-0`，从 `Content-Range` 获取源文件总字节数。BIN 缺失时探测 TXT。确认预算包括 points3D 和 points_t，避免点时间表偷偷大额下载；UI 分别列出几何、时间体积。
- 阈值：`colmap4dResources.ts:2` 的 `LOAD_CONFIRM_BYTES = 200 * 1000 * 1000`，十进制 200 MB。与点数量阈值完全独立。
- 采用 viewer 上方两条常驻按钮行，图片 / 点云各自“加载 / 跳过”。跳过后仍可补载，失败可重试；无嵌套弹窗。小于等于阈值自动加载，超过或未知必须选择。

## 加载时序、抽样与限制

1. Worker 先加载相机、位姿、时间表。BIN images 使用 Range 跳过 observation payload：真实 1,007,776,898 字节 images.bin 仅请求 82 个短片段，共 331,784 字节，再加 cameras / times / meta 约 2.6 KB。拒绝把不支持 Range 的 200 响应完整读入内存。TXT 保留流式解析，必须扫描文本才能跨过观测行，无法承诺相同的快速 seek。
2. 先交付空点云场景，再测量并决定两个重资源的加载。确认前仅点云的两次 1 字节探测，无完整点云 / 点时间请求。
3. 下载按资源增量进行；ColmapUtil 的现有 `processFiles` 是整份文件集解析，因此桥接缓存已读的 Blob，将新增资源与缓存组合后重建 iframe 场景，避免并发 processFiles 覆盖。**补载会重新解析有界场景并重置视角**，但不重新下载相机或已加载资源。此次没有改变 ColmapUtil 的其它文件处理行为。
4. 点云确认后才开始全表流式扫描，最多 200,000 点。保留原始 ID、ns 和按 ID 关联的点时间。确认只授权读取，绝不关闭抽样。真实数据 41,990,400 点最终保留 199,955 点；默认 10% 时间窗下实测绘制 19,749 点实例，切换时间可看其它部分。抽样不代表只下载 20 万点的字节，源点云及时间表仍需完整扫描。
5. 取消预览会终止 Worker、取消图片/metadata 请求。相机短请求设超时，失败明确显示重试；metadata 失败进入手动询问，不默认为小文件。每次重建 iframe 仅发送一次数据，避免 viewer 的重复 ready 通知引起循环解析。

## 验证和截图

真实模型为 `output/hololab/w/9967b1f8-3646-41f5-a269-394b6d392169/j/5eba9322-2842-46ff-8e61-b0c8f63cb760/colmap4d`。实测图片 132,098,827 B（132.1 MB / 126 MiB）、points3D 2,477,433,608 B（2.48 GB / 2.31 GiB）、points_t 671,846,408 B；合并提示 3.15 GB。

真实数据截图来自独立 Vite 测试端口 15175，直接代理到**正在运行的同一文件服务 8829**；目录 stat 仍经过真实 gateway/node RPC。只替换 host 测试页面，没有模拟数据文件。使用真实 Colmap4DPreview、Worker 和 ColmapUtil，浏览器无 pageerror。

- [cams 预览](colmap4d-staged-direct/cams.png)
- [相机先显示、图片自动加载、点云等待确认](colmap4d-staged-direct/ask.png)
- [跳过点云](colmap4d-staged-direct/skip.png)
- [补载点云及抽样结果](colmap4d-staged-direct/loaded.png)
- [真实请求和 WebGL 证据](colmap4d-staged-direct/evidence.json)

图片本身未超过阈值，额外测试**仅将目录 stat 体积模拟增加 201 MB**，像素文件仍是真实的 81 张。验证两个询问独立存在，双方跳过后只加载图片，点云保持跳过；同意前 0 个像素请求。这是边界交互测试，不能称为真实 333 MB 数据：

- [两项同时询问（模拟图片体积）](colmap4d-image-gate/both-ask-simulated-size.png)
- [两项跳过（模拟图片体积）](colmap4d-image-gate/both-skipped-simulated-size.png)
- [只补载图片（模拟图片体积）](colmap4d-image-gate/skip.png)

自动验证：10 项前端相关单测；2 项 Python 映射/旧 handle 推断测试；TypeScript 与生产 build 通过。普通 COLMAP 在生产静态资源上的 smoke 通过：3 个 pose、125 个点，0 网络失败、0 pageerror。splatv / ftgsply 源码及入口未修改。

## 未解决的线上传输问题

15174 使用真实 gateway `/proxy` 反向隧道时，连续 Range 请求出现 502、短读、挂起，另一次 metadata 请求返回了非 JSON 的二进制。磁盘元数据正常，直连相同文件服务的上述流程通过。额外 Python 连续 Range 请求也复现 HTTP 502 / IncompleteRead，说明不只发生在 React 渲染中。不能据此断言唯一根因；早期关于 Chromium 缓存的判断未获充分证实。

最终线上复测明确失败于 `Camera Range: HTTP 502`，见 [错误截图](colmap4d-staged-live-final/transport-failure.png) 和 [请求记录](colmap4d-staged-live-final/evidence.json)。本轮已避免主动取消成功的微小 Range 探测并增加超时/错误显示，但这没有修复运行中隧道。没有修改或重启它。后续应单独排查 tunnel 的 cancel/end、连接归还与 body queue 隔离，再安排用户允许的服务更新；本报告不宣称线上大模型验证通过。

## 改动位置

| 文件（相对 hololab 仓库） | 内容 |
|---|---|
| `hololab/gateway/tag_viewers.py:46` | cams 默认 viewer 与根目录 member |
| `hololab/frontend/src/canvas/NodePreviews.tsx:50` | cams 专用预览注册 |
| `hololab/frontend/src/canvas/previews.tsx:220` | 旧服务前端 fallback、组件路径、目录元数据上下文 |
| `hololab/frontend/src/canvas/Colmap4DPreview.tsx:12` | 阶段生命周期、按钮行、资源重试、缓存与重建、自动取景消息 |
| `hololab/frontend/src/canvas/colmap4dPayload.ts:54` | RangeReader；`:78` 分阶段解析；`:268` 相机取景范围 |
| `hololab/frontend/src/canvas/colmap4d.worker.ts:2` | Worker phase 协议 |
| `hololab/frontend/src/canvas/colmap4dResources.ts:2` | 阈值；`:8` Range stat；`:29` 递归目录 stat |
| `hololab/frontend/src/canvas/colmap4dPayload.test.ts` / `colmap4dResources.test.ts` | 阶段网络隔离、抽样、范围拒绝、元数据与路径测试 |
| `tests/test_colmap4d_viewer.py:5` | cams 映射回归 |
| `hololab/frontend/scripts/smoke-colmap4d-staged.mjs` | 真实数据可复现浏览器验证及可选图片体积边界测试 |
