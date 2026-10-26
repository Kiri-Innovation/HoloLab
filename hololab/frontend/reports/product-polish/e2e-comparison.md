# 完整浏览器回归对照

当前完整运行：99 passed / 35 failed / 1 skipped。以下 35 个失败用例，在未修改的 HEAD 前端副本中也观察到失败。该表证明失败用例已存在，不代表已修复，也不以旧失败代替本次专项验证。

HEAD 副本仅调整 Vite 端口为 4174 与缓存目录，未修改产品源码。`inspector-sidebar` 原测试硬编码 5173，另以 `INSPECTOR_URL=http://127.0.0.1:4174` 单独复核，仍失败：[日志](baseline-sidebar.log)。live run-reference 没有启用，沿用原跳过条件。

| 当前失败用例 | HEAD 副本 |
| --- | --- |
| e2e/connection-feedback.spec.ts:6:3 › drag validation anchored feedback dark | 失败 |
| e2e/connection-feedback.spec.ts:6:3 › drag validation anchored feedback light | 失败 |
| e2e/enum-inspector.spec.ts:3:37 › enum controls preserve raw values (legacy=false) | 失败 |
| e2e/inspector-preview.spec.ts:3:92 › shared inspector preview custom | 失败 |
| e2e/inspector-preview.spec.ts:3:92 › shared inspector preview hidden | 失败 |
| e2e/inspector-preview.spec.ts:3:92 › shared inspector preview legacy | 失败 |
| e2e/inspector-scroll.spec.ts:3:1 › inspector sections share a scroller and preserve position across tabs | 失败 |
| e2e/inspector-sidebar.spec.ts:5:1 › sidebar selection, autosave, assignment, deletion and theme screenshots | 失败 |
| e2e/node-creation-menu.spec.ts:6:1 › search, keyboard creation and palette drops share pan/zoom coordinates | 失败 |
| e2e/node-creation-menu.spec.ts:83:1 › dismissal, builtin, edge clamping and right-button isolation | 失败 |
| e2e/node-title-tooltip.spec.ts:5:3 › node title tooltip in dark | 失败 |
| e2e/node-title-tooltip.spec.ts:5:3 › node title tooltip in light | 失败 |
| e2e/open-artifact-location.spec.ts:416:3 › footer spacing in dark theme | 失败 |
| e2e/open-artifact-location.spec.ts:416:3 › footer spacing in light theme | 失败 |
| e2e/package-identity.spec.ts:3:3 › package identity alias: display and save | 失败 |
| e2e/package-identity.spec.ts:3:3 › package identity dev: display and save | 失败 |
| e2e/package-identity.spec.ts:3:3 › package identity legacy: display and save | 失败 |
| e2e/parameter-pins.spec.ts:102:1 › pinned array, path, bool and list controls share the Inspector editors | 失败 |
| e2e/parameter-pins.spec.ts:130:1 › transform pin toggles x/y/scale as one row and grows node bounds | 失败 |
| e2e/parameter-pins.spec.ts:150:1 › legacy enum pins preserve invalid values until explicitly edited | 失败 |
| e2e/parameter-pins.spec.ts:177:1 › manifest defaults appear immediately and first toggle freezes an explicit override | 失败 |
| e2e/parameter-pins.spec.ts:38:1 › workflow pins share display, not values, and survive save/reload | 失败 |
| e2e/port-tooltip.spec.ts:3:40 › port and edge tooltips dark | 失败 |
| e2e/port-tooltip.spec.ts:3:40 › port and edge tooltips light | 失败 |
| e2e/port-tooltip.spec.ts:52:39 › three rows with runtime data dark | 失败 |
| e2e/port-tooltip.spec.ts:52:39 › three rows with runtime data light | 失败 |
| e2e/recent-run-link.spec.ts:3:38 › recent preview links to its attributed run dark | 失败 |
| e2e/recent-run-link.spec.ts:3:38 › recent preview links to its attributed run light | 失败 |
| e2e/reference-quote.spec.ts:6:3 › reference quote matches adjacent node controls in dark theme | 失败 |
| e2e/reference-quote.spec.ts:6:3 › reference quote matches adjacent node controls in light theme | 失败 |
| e2e/resource-lens-scroll.spec.ts:3:1 › bottom compute dock keeps the device visible while the lens scrolls | 失败 |
| e2e/resource-metrics.spec.ts:6:1 › four metric lenses, host process menus, return focus and narrow layout | 失败 |
| e2e/toast-canvas.spec.ts:6:3 › toast is a canvas-centered black pill in dark theme | 失败 |
| e2e/toast-canvas.spec.ts:6:3 › toast is a canvas-centered black pill in light theme | 失败 |
| e2e/view-node.spec.ts:15:1 › view sticker survives drag, save, reload, selection and deletion without an algorithm pack | 失败 |
