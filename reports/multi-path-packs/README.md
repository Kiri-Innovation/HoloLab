# 同名同版本多路径验证

实现保留 first-wins，并把落选副本带到详情页。没有重启或热替换运行中的 hololab，也没有改实装包、访问 4090 或发布注册表探针。

## 数据链路与兼容性

原 `hololab/node/packs.py:257` 的重复分支直接 `continue`，丢掉后续副本；现重复分支在 `:279` 附近保存 `shadowed`，同一 manifest 的重复扫描/软链接去重。执行仍只使用第一份。

节点 inventory 新增 `locations`；`/api/pack-catalog` 新增 `locations_by_node[node_id]`，避免不同机器路径混淆。每项包括 `pack_dir`（manifest 所在目录）、`manifest_path`、`root_abs`（声明的内容根）、`source_dir`（扫描入口）、`source_type`、`effective`、`manifest_hash`、可空的 `code_sha256`。来源类型为 `community_install`（有安装凭据）、`source_scan`（包括手动配置的 local-packs；具体扫描入口保留）、`bundled`。

现有 `source_dir`、`manifest_path`、`content.root_abs` 等字段和 first-wins 行为不变。旧节点缺少新字段时默认空列表；前端继续显示旧路径。

启动扫描复用已加载的 manifest 及其哈希，不遍历落选副本内容。生效者复用现有内容计算结果；落选者 `code_sha256=null`。前端区分内容哈希不同、清单哈希不同和内容哈希未知，绝不把清单相同当成代码相同。

## 实测方式与边界

`api-evidence.json` 是读取 A800 上真实安装目录与 SpacetimeGaussians 源码目录，通过当前代码的隔离 FastAPI TestClient `/api/pack-catalog` 得到的响应（临时 SQLite）。使用 `verify.py` 可在仓库根复现。扫描、两份生效包原有内容计算及 API 读回共约 3.43 秒；并非启动性能基准。

- `k4d/stg-train@0.4.8`：两条路径，社区安装 `effective=true`，源码 `effective=false`，两份 manifest 哈希不同。
- `k4d/stg-train@0.4.7`：一条源码路径，生效。
- `page.png`：浏览器加载本次前端，以上述真实 API JSON 回放；展示两条 pack 路径、覆盖关系、清单差异与未知内容哈希说明。
- 浏览器单份回归：只有一个原有「复制路径」按钮，无多路径区域。

**8828 现有进程尚未加载新后端代码**：只读 GET 仍返回原有 `source_dir=/root/.hololab/node/packs`，没有 `locations_by_node`。此次证据是隔离新代码验证，不宣称线上已生效；遵守不重启约束。

## 检查

- 后端：`tests/test_pack_locations.py tests/test_pack_dirs.py tests/test_pack_install_dir.py tests/test_pack_reload.py`，32 passed。
- 前端：`npm run build` 通过（Vite 大 chunk 提示）。
- 浏览器：`e2e/pack-multi-path.spec.ts`，4 passed；覆盖真实双路径、单路径、清单/内容哈希差异提示。
- 本机 Python 缺测试依赖，安装到 `/tmp/multi-path-deps` 并通过 PYTHONPATH 使用，没有修改服务 Python 环境。浏览器依赖复用 `/tmp/hololab-*-browser-libs`。

SpacetimeGaussians 的 `.packignore` 属于另仓，需另仓提交，本次未操作。
