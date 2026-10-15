# 画布右键节点创建菜单

## ASE 对照

参考 [Amplify Shader Editor 官方手册](https://wiki.amplify.pt/index.php?title=Unity_Products%3AAmplify_Shader_Editor%2FManual)：右键画布打开可搜索节点列表，点击创建；Node Palette 拖拽仍可使用。

本次实现：空白处右键、光标处菜单（窗口边缘自动避让）、搜索自动聚焦、大小写不敏感子串过滤、共享 Packs 文件夹分类和内置 visualize、分组折叠、空搜索显示全部、内部滚动、↑↓循环选择并滚动高亮项、Enter 创建、Esc / 外部点击关闭、无匹配状态、深浅主题。宽度 304px，最大高度 480px，并受窗口大小约束。

未实现 ASE 的其他功能：拖线到空白处自动弹菜单并连线、Space 打开菜单、字母快捷键直接创建。这些不在本次范围内。按本次要求，右键不平移；保留左键/中键平移与原有 Shift 框选配置。

## 实现定位

- `src/canvas/packTree.ts:21` / `:87`：从 Packs 面板原样提取分类树与搜索函数；内置 visualize 只在这里注册。两个入口共用 App 的 catalog，没有新增节点注册表。
- `src/canvas/PackPalette.tsx:303`：原面板改为调用共享模型，保留拖拽协议与持久化分组状态。
- `src/canvas/NodeCreationMenu.tsx:9`：菜单、键盘、焦点、外部点击、分组折叠和边缘避让。搜索匹配 pack/node 名、描述和分类路径。
- `src/canvas/nodeCreationMenu.css:1`：现有主题变量、尺寸及内部滚动。
- `src/App.tsx:1389`：将原 onDrop 的创建逻辑提取为 createNodeAtScreen，继续使用原有 rfInstance.screenToFlowPosition；菜单与拖拽均调用它。取消首次用户创建时尚未执行的初始 fitView，避免空图第一节点创建后视图跳走。
- `src/App.tsx:2652`：只接受 ReactFlow pane 自身的 contextmenu；不 preventDefault 节点、端口、连线、控件事件。
- `src/App.tsx:2679`：菜单接入。菜单显示位置可避让，但创建始终使用原右键坐标。

## 验证

命令均在 `hololab/frontend` 下运行：

- `npm run build`：通过（现有大 chunk 提示，不影响构建）。
- `npm test`：18 个文件、505 项测试通过。
- `npx playwright test e2e/node-creation-menu.spec.ts e2e/view-node.spec.ts --workers=1`：4 项通过。

新增 e2e 使用 `e2e/fixtures/node-creation.ts` 拦截 API / WebSocket，测试生产 App；覆盖真实滚轮缩放和拖动平移后的屏幕/画布落点、搜索、键盘、折叠、长列表滚动、内置节点、无匹配、关闭、窗口边缘、右键不平移、节点/端口/连线/控件事件隔离、Backspace 隔离、Shift 框选及原面板拖拽协议。既有 view-node 回归覆盖保存、重载、选择和删除。

薄 SVG 连线和端口用 DOM contextmenu 事件测试传播与是否被取消；菜单打开、选择、创建和拖动平移使用浏览器鼠标/键盘操作。截图采用固定 fixture catalog，不依赖本机在线 packs。

## 截图

- [菜单展开（浅色）](../e2e/screenshots/node-creation/expanded-light.png)
- [菜单展开（深色）](../e2e/screenshots/node-creation/expanded-dark.png)
- [搜索过滤](../e2e/screenshots/node-creation/search.png)
- [缩放平移后创建位置](../e2e/screenshots/node-creation/created-position.png)


检查时另发现已有算法节点删除后的重水合逻辑：App 的 catalog 恢复 effect 在已保存图仍引用这些节点时可能将其恢复。本次未修改该逻辑；删除快捷键回归沿用现有 view-node 测试，新增测试验证菜单内按键不会外泄。
