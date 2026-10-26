# 全量失败对照

本轮：120 passed / 25 failed / 1 skipped。上一轮：99 passed / 35 failed / 1 skipped。按文件和测试标题比对（忽略行号变化），本轮 25 项失败均已出现在上一轮失败集合中。此前报告还保存了改动前基线复现结果；本次未重跑旧版本基线，不将标题匹配等同于重新证明每个失败的根因。

本轮失败：

- e2e/connection-feedback.spec.ts › drag validation anchored feedback dark
- e2e/connection-feedback.spec.ts › drag validation anchored feedback light
- e2e/enum-inspector.spec.ts › enum controls preserve raw values (legacy=false)
- e2e/inspector-preview.spec.ts › shared inspector preview custom
- e2e/inspector-preview.spec.ts › shared inspector preview hidden
- e2e/inspector-preview.spec.ts › shared inspector preview legacy
- e2e/inspector-scroll.spec.ts › inspector sections share a scroller and preserve position across tabs
- e2e/inspector-sidebar.spec.ts › sidebar selection, autosave, assignment, deletion and theme screenshots
- e2e/node-creation-menu.spec.ts › search, keyboard creation and palette drops share pan/zoom coordinates
- e2e/open-artifact-location.spec.ts › footer spacing in dark theme
- e2e/open-artifact-location.spec.ts › footer spacing in light theme
- e2e/parameter-pins.spec.ts › legacy enum pins preserve invalid values until explicitly edited
- e2e/parameter-pins.spec.ts › manifest defaults appear immediately and first toggle freezes an explicit override
- e2e/parameter-pins.spec.ts › pinned array, path, bool and list controls share the Inspector editors
- e2e/parameter-pins.spec.ts › transform pin toggles x/y/scale as one row and grows node bounds
- e2e/parameter-pins.spec.ts › workflow pins share display, not values, and survive save/reload
- e2e/port-tooltip.spec.ts › port and edge tooltips dark
- e2e/port-tooltip.spec.ts › port and edge tooltips light
- e2e/port-tooltip.spec.ts › three rows with runtime data dark
- e2e/port-tooltip.spec.ts › three rows with runtime data light
- e2e/recent-run-link.spec.ts › recent preview links to its attributed run dark
- e2e/recent-run-link.spec.ts › recent preview links to its attributed run light
- e2e/resource-lens-scroll.spec.ts › bottom compute dock keeps the device visible while the lens scrolls
- e2e/resource-metrics.spec.ts › four metric lenses, host process menus, return focus and narrow layout
- e2e/view-node.spec.ts › view sticker survives drag, save, reload, selection and deletion without an algorithm pack
