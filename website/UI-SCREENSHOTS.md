# 文档真实 UI 插图采集记录

案例仅为获准公开的 classic STG（`3711480d`）。两种语言共用英文为主的真实产品截图，每张均有浅/深 WebP，按 2x 采集。

## 插图与体积

文件位于 `public/images/docs/`；下面尺寸是文件像素。每个文件的精确字节数另见 `public/images/docs/manifest.json`。

| 文档 | 图 | 尺寸 | 浅色字节 | 深色字节 | 脱敏与取景 |
| --- | --- | --- | ---: | ---: | --- |
| quick-start | 工作流列表（[gallery 浅](public/images/docs/gallery-light.webp) / [深](public/images/docs/gallery-dark.webp)） | 1800 × 600 | 10338 | 9670 | 只保留 STG 一项；名称、更新时间隐藏 |
| quick-start | 画布全貌（[canvas 浅](public/images/docs/canvas-light.webp) / [深](public/images/docs/canvas-dark.webp)） | 3200 × 2100 | 106580 | 96476 | 隐藏工作流名、设备标识、路径、时间、原始影像；仅保留获批点云 |
| quick-start | Packs + Compute nodes（[panels 浅](public/images/docs/panels-light.webp) / [深](public/images/docs/panels-dark.webp)） | 1294 × 664 | 16384 | 14626 | 两个真实局部并列；设备名称/ID 隐藏 |
| workflows | Pack 与已有节点（[add-node 浅](public/images/docs/add-node-light.webp) / [深](public/images/docs/add-node-dark.webp)） | 1400 × 664 | 17988 | 16200 | 两个真实局部并列；未添加节点 |
| workflows | 端口与连线（[ports 浅](public/images/docs/ports-light.webp) / [深](public/images/docs/ports-dark.webp)） | 1470 × 380 | 12722 | 11798 | 保留已有连接；隐藏预览与设备标识 |
| workflows | Run 工具栏（[run 浅](public/images/docs/run-light.webp) / [深](public/images/docs/run-dark.webp)） | 726 × 104 | 3562 | 3258 | 裁去项目名；未点击 Run；当前没有可见的 Saved 标记 |
| run-records | Run history + Recent jobs（[runs-jobs 浅](public/images/docs/runs-jobs-light.webp) / [深](public/images/docs/runs-jobs-dark.webp)） | 1054 × 714 | 18738 | 17372 | 两个局部并列；隐藏 ID/时间；任务面板真实为空 |
| run-records | Artifacts（[artifacts 浅](public/images/docs/artifacts-light.webp) / [深](public/images/docs/artifacts-dark.webp)） | 3200 × 1140 | 53020 | 47626 | 服务端按 STG 过滤；隐藏文件名、句柄、路径、时间、其他工作流选项 |
| run-records | 节点点云预览（[preview 浅](public/images/docs/preview-light.webp) / [深](public/images/docs/preview-dark.webp)） | 844 × 1236 | 51282 | 48732 | 只保留 STG colmap-triangulate 点云；隐藏设备、句柄、路径及预览器文字 |

合计 556,372 字节（543.3 KiB）。局部均低于 200 KiB，全貌均低于 400 KiB。

## 范围与只读保障

- 列表采用只保留 STG 卡片的方式：浏览器 DOM 中移除其他卡片，再截取列表局部。未更改服务端工作流。
- 不点击 Run，不创建/删除/编辑节点或连线，不启动计算。搜索 Packs、选择 Artifacts 筛选项属于本地浏览。
- 采集禁用代理，拦截所有非 GET/HEAD/OPTIONS 的 HTTP 请求；完整采集挡住页面自动发起的 2 次 POST，后续局部补采没有写请求。原始采集和审计文件留在忽略的 `test-results/doc-ui/`，不提交。
- Artifacts 的 GET 请求附加当前 STG 的 `workflow_id`，使用服务端真实筛选结果，未伪造响应或产物。没有点击 Check liveness。
- 原始视频/图片请求被拦截，截图中仅保留已获准的点云预览；通过 DOM 隐藏元数据、裁切、并列局部及 WebP 压缩完成脱敏，不生成或补造 UI。
- 全景保留 Packs / Compute nodes / Run history / Recent jobs 这些被介绍的面板，移除其中敏感内容。端口局部与工具栏局部均来自同一工作流。

## 当前 UI 的实际情况

中文标签包括“视图贴纸”“引用整张流程”“陈旧”“定位”“草稿有结构改动”“运行将创建新快照”“检查”“当前”。英文版首次相关图注注明部分产品标签仍为中文，未在截图里伪造英文界面。

运行记录的可见标题是 **Run history**；文档其他段落沿用 **Runs** 概念名。采集时 **Recent jobs** 显示 **No jobs yet.**，因此图片只介绍入口，不能展示正在执行的任务或日志。Run 工具栏图展示实际的 Run 按钮，没有强行加入当时不可见的保存状态。

## 接入与复现

- 中英各三篇、每篇三图，均使用 `<picture>`、本地化 alt 和 figcaption。
- `screenshots` 仅可用于本站内容，构建只接受 `public/images/docs/` 下存在的本地浅/深 WebP；HTML 仍经白名单净化。
- 网站手动切换主题时更新 picture source，使图片跟随站点主题，而非只跟随操作系统。图片宽度不超出正文。
- `scripts/capture-doc-ui.mjs` 只读采集；`scripts/prepare-doc-ui.py`（Pillow）组合/压缩；`tests/doc-screenshots.mjs` 验证 18 份资源、主题、双语和窄屏。
- 再次采集须重新人工审图：脱敏规则针对当前 UI，不能保证未来新增字段自动被遮蔽。不要提交原始截图或检查数据。

```sh
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy node scripts/capture-doc-ui.mjs
python scripts/prepare-doc-ui.py
npm test
npm run build
# 另开终端启动 preview --port 5259 后
node tests/doc-screenshots.mjs
```

## 插入位置

- `content/en/use/quick-start.md`：第 53、65、74 行。
- `content/en/use/workflows.md`：第 18、37、46 行。
- `content/en/use/run-records.md`：第 41、92、99 行。
- `content/zh/use/quick-start.md`：第 53、65、74 行。
- `content/zh/use/workflows.md`：第 18、37、46 行。
- `content/zh/use/run-records.md`：第 41、92、99 行。

## 验证产物

`test-results/doc-screenshots/` 包含 18 张单图效果截图，以及 quick-start / workflows / run-records 的中英 × 浅深桌面和移动端整页截图。`verification.json` 记录通过结果：1440px / 390px、无页面错误、无横向溢出、图片主题正确。构建通过，Vite 提示主 JS 超过 500 kB；这是构建体积警告，图片独立加载。
