# colmap4d 画布预览接入与验证

2026-11-06。代码已实现；验证使用本机临时开发入口。未部署、未重启生产服务、未 push。没有修改任何 pack 或 ColmapUtil 源码。

## 现状核实

- `colmap4d-split@0.1.0` 实际输入完整 colmap4d，输出 cams / point-cloud / images 三个组件；完整模型来自 merge / MegaSaM。此次只注册完整 `colmap4d`，不将组件冒充完整模型。
- 原 COLMAP 链路：`hololab/frontend/src/canvas/NodePreviews.tsx:48` 识别前端 viewer tag；`previews.tsx:337` 按 colmap/colmap-folder/colmap-frame 分派；`:3994` 将 handle proxy URL 补成 `sparse/0`，图片目录另传；`:3616` 浏览器读取 cameras.txt、images.txt、points3D.txt，缺少的组件用空 TXT 占位，按图片名读取缩略图，并缓存 payload；`:3843` 在 iframe `colmap-ready` 握手后 postMessage `{type:'colmap-load-files', files:[{name,blob}]}`；`:3893` 使用 `/colmaputil/index.html?embed=1`。网关/节点文件服务提供原始文件，不在这条桥中转 BIN 为 TXT。
- 原桥只接受三份 TXT（及图片缩略图），没有 times / points_t / time_meta。完整 colmap4d 既没有默认声明，也没有前端路由，所以之前显示目录信息。
- ColmapUtil 已有可执行的 4D 支持，入口仍是普通 `embed=1` 文件集合，无须增加 embed=colmap4d。vendor `src/components/initiation-page/InitiationPage.jsx:117` 将传来的 Blob 包装为 File，调用 processFiles；`fileDropHandler.js:216` 按文件名识别 times.bin/txt、points_t.bin/txt、time_meta.json；`:97` 按 IMAGE_ID / POINT3D_ID 关联时间。`src/codec/decode/sidecar.js:32,56,68` 读取 BIN 时间和 JSON，TXT 也支持。`src/components/visualizer/OverlayUI.jsx:1326,1504` 是底部时间条；`ColmapVisualizer.jsx:750` 为 CPU 点过滤，`:849` 为 GPU 窗口，`:923` 为相机视锥过滤，`:1078` 为图像平面过滤。以上路径位于 `hololab/frontend/vendor/ColmapUtil/`，独立仓 `../Utils/ColmapUtil/` 对应功能也存在。
- 支持程度：本次真实数据证实点云、相机、时间条可加载交互，不是占位半成品。但不等于完整大模型加载能力：原解析器逐点建对象，原时间文件失败会警告后继续静态加载；`src/utils/timeAxis.js:17` 将绝对 ns 转 Number，仍有 epoch ns 低位精度限制。本次桥保留 int64 ns，不承诺修复渲染器原有的纳秒级精度问题。

## 接入与预算

使用既有 OutputPreview 声明机制，可写：

```yaml
outputs:
  model:
    tags: [colmap4d]
    storage: dir
    preview:
      viewer: colmap4d
      member: sparse/0
```

同时在 `tag_viewers.py` 注册同样的默认声明，因此现有 merge / MegaSaM 产物不必重跑，也不必更改已发布 pack 的不可变 manifest。显式 preview 声明优先。现有先例为 `packages/FreeTimeGsVanilla/packs/freetimegs-to-viewer/manifest.yaml:72` 的 `viewer: ftgsply / member: model.ftgs.ply`。

选择 (c)：小模型全量点，超过 **200,000 点**自动抽样。BIN 按已知总数确定性等间隔取样；TXT 使用确定性 reservoir，保证没有计数头时内存仍受限。读取发生在独立 Web Worker，响应流逐块消费，跳过 images 的 POINTS2D 和 points3D 的 tracks；只有入选点交给现有 ColmapUtil 对象解析器。相机内参、图像位姿保留，暂不额外下载图片纹理。预览不是可导出的完整重建，观测/track 查询不具有原模型的完整性。

时间桥读取 times.bin、points_t.bin、time_meta.json（BIN 不存在时允许对应 TXT）；按原 ID 筛选点时间并保留精确 ns，缺少某个点的时间继续表示时间无界。不依赖 ID 排序，不将点位置与时间按行号拼接。时间文件截断/缺失报可见错误，避免默默显示静态模型。JSON 原样送入 iframe。总点数、显示点数、“预览抽样”、合成/估计时间在窗口上方标明。

这是**流式读取 + 有预算的抽样显示**，不是全量 GPU 点云流式渲染。仍扫描完整原始表，真实六份表共 4,157,059,495 字节；首次读取约 34–35 秒（本机开发入口测量，不是远程网络承诺）。关闭预览/切换元素会终止 Worker，取消旧读取；不缓存多份巨型原始表。相机/元数据和图像头另外设大小上限。

## 改动位置（相对 HoloLab 仓库）

| 文件 | 内容 |
|---|---|
| `hololab/manifest/schema.py:192` | 允许 `preview.viewer: colmap4d` |
| `hololab/gateway/tag_viewers.py:47` | 类型默认声明，现存 handle 读取时自动推断 |
| `hololab/frontend/src/wire.ts:200` | 前端声明类型 |
| `hololab/frontend/src/canvas/NodePreviews.tsx:49` | 旧网关/历史 handle 的画布预览入口 |
| `hololab/frontend/src/canvas/previews.tsx:219` | 声明分派、类型后备、数组分页；其余 viewer 分支不变 |
| `hololab/frontend/src/canvas/Colmap4DPreview.tsx:9` | iframe 握手、Worker 生命周期、抽样说明、重试、现有画布交互盾 |
| `hololab/frontend/src/canvas/colmap4d.worker.ts:1` | 后台读取与错误/进度消息 |
| `hololab/frontend/src/canvas/colmap4dPayload.ts:49` | 流式预算读取；`:98` 位姿、`:133` 点抽样、`:167` 时间桥 |
| `hololab/frontend/src/canvas/colmap4dPayload.test.ts:1` | BIN/TXT、跨网络块、变长 tracks、原 ID/ns、缺失点 t、截断 sidecar |
| `tests/test_colmap4d_viewer.py:1` | 声明、推断、显式覆盖、组件排除、历史 handle GET |

## 验证与截图

- A800 真实工作流 `9967b1f8-3646-41f5-a269-394b6d392169`，当前画布 MegaSaM 0.7.0 完整产物，**41,990,400 点 / 81 images / 1 camera**；截图不是 mock 数据。另核对历史 merge 0.3.0 job `a31dba7e-9ca5-4c04-8d4b-c915a5a30937` 的 handle `ef8b1814-fc04-4e60-93ea-ec97a5dc2323`、六份 BIN/JSON 文件及相同点数。当前草稿已不含该历史 merge 节点，未为截图修改工作流。
- 实际展示 **199,955 / 41,990,400 点**。时间条从 0.000 移到 1.867 / 2.667 秒，窗宽 ±266.7 ms；查看 `browser.json` 和前后截图。截图视角通过既有 camera 消息调整，未修改数据或渲染器。
- [完整画布，初始时间](canvas-start.png)、[完整画布，移动时间后](canvas-time.png)、[小窗，初始时间](node-start.png)、[小窗，移动时间后](node-time.png)。截图在开发入口 `127.0.0.1:5175` 获取。
- `npx vitest run src/canvas/colmap4dPayload.test.ts`：4 passed。
- `pytest tests/test_colmap4d_viewer.py tests/test_ftgsply_viewer.py tests/test_ply_3dgs_viewer.py -q`：11 passed。
- `npx playwright test e2e/preview-dispatch-regression.spec.ts e2e/viewer-focus.spec.ts --workers=1`：5 passed，覆盖既有 splatv 分派、文本/二进制保护、COLMAP 等交互门控。不是宣称重新验证了所有 splatv/ftgsply 渲染能力。
- `npx tsc -b` 与 `npx vite build --outDir /tmp/hololab-colmap4d-dist` 成功。构建保留现有的大 chunk 提示。没有覆盖线上 dist。

## 正式服务异常与部署边界

真实文件服务 `http://127.0.0.1:8829/w/.../model/sparse/0/cameras.bin` 返回正确的 64 字节 BIN；同一文件经 `8828/proxy/{node}/w/...` 访问，得到 HTTP 200、原 BIN 响应头，但正文是 `{"files":[]}` 或其它 glob JSON。images.bin 请求也复现。增加 query 未消除。这里仅报告实测现象，没有定位根因或假定重启一定修复。

为了不重启现有服务，临时 Vite 5175 仍连接真实 8828 API/WS，但 `/proxy/{node}` 在该临时开发配置中直连真实 8829。没有 mock、截取替代模型、写数据库或启动算法 job；没有把此临时代理写入产品配置。因此截图证明新前端 + 真实文件服务 + 现有 ColmapUtil 渲染链，**不代表当前 8828 正式代理已经修复**。

用户统一部署时需要：重新构建并部署 HoloLab 前端（常规 `npm run build`），重载 gateway 让 schema/tag registry 生效；无需重跑 merge/MegaSaM。前端有历史 handle 类型后备，但正常网络读取仍要求修复/复验上述 `/proxy` 正文异常。无需因本任务单独更换算法环境或重启计算 job。

## Git / submodule

独立 `Utils/ColmapUtil` 保持 `6fe52a73115d1213f5dbfccb645acd9360c295e9`，vendor 保持 `a1d047ebb81a7c9efcc91ceb4bbf8cba80c602d4`，两者源码工作区均干净；没有改上游、没有更新 vendor gitlink。两者本来不同，不强行对齐。

本次只在 HoloLab 仓提交上述相关文件和验证报告。主仓原有 hololab 等 submodule 在途变化不打包提交；其它会话改动和报告不处理。使用本机 git config 署名，英文 feat 提交，不 push。
