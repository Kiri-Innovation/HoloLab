# Pack Manager：社区浏览与本地安装

实现已完成并提交前验证。**公网成功安装验收尚未完成，全量浏览器回归也未全绿**，不能把这两项标为通过。

## 页面与安装行为

`/packs` 保留唯一包入口，页内切换“本地 / 社区”，切换不丢失社区安装结果。本地保留环境、工作流使用情况与配方检查，并补充来源路径、注册表来源与代码身份。普通目录包可通过“查询当前发布状态”读取当前代码身份；未读取时不伪装为已校验。注册表安装收据中的哈希直接可见。

社区先请求目录，选中包再请求它的版本元数据。展示版本、发布时间、发布者、yank、代码 SHA-256、runtime.env 与 requires；同名同版本按所选节点标记已安装。缺省源是 `https://packs.xenotech.studio/`。源加载、空目录、无版本、源不可达和包不存在均有反馈。yanked 按钮禁用，后端也重新检查。未声明 requires 与缺失环境继续区分；安装不会自动安装环境依赖。

安装直接执行，不二次确认；显示“下载、校验并安装”的不定进度，不伪造百分比。成功后节点主动重扫并发送 packs_updated，页面刷新本地目录与环境检查。失败只返回可读原因，不把异常堆栈或底层调试文本放进 UI。已有目录不覆盖。

## 接口、联网与落点

- `GET /api/packs/registry?source=<HTTP(S)根地址>&name=<可选scope/name>`：不带 name 拉 `/packs/`，返回 `{projects:[{name}]}`；带 name 拉 `/packs/<scope>/<name>/`，返回经校验的 Index。限制元数据大小为 16 MiB、远端读取超时 15 秒；不读全站 index.json。
- `POST /api/packs/install`：JSON `{spec, source, node_id, expected?}`。spec 是精确版本；node_id 明确安装目标；UI 必传所浏览版本的 expected code_sha256。成功返回安装器结果及 destination；失败返回 `detail: {code,message}`。错误涵盖 hash_mismatch、yanked、source_unavailable、not_found、disk_error、conflict、invalid_package、no_destination、node_unavailable。
- 新增节点协议 `pack_install_req / pack_install_resp` 与 `pack_install_v1` 能力位。旧节点明确提示不支持，不会假装安装成功。网关经既有相关请求机制等待节点，最多 600 秒；断线/超时页面提示检查后重试。

选择**网关代拉目录，计算节点下载并安装**。浏览器无需跨域或访问公网，网关能够报告目录可用性；网关和目标节点分别需要访问源。浏览器直连虽省一个代理，但依赖浏览器网络与注册表 CORS，也不能证明节点能下载。没有把远程节点的文件错误地写在网关磁盘上。

落点严格沿目标节点的 `pack_dirs` 扫描规则选择第一个包集合目录：跳过 manifest 文件和单包目录；仓库根配置递进到其 `packs/` 容器；尚不存在的目录配置可创建。最终布局：

```
<集合目录>/<scope>/<name>@<version>/.hololab-install.json
<集合目录>/<scope>/<name>@<version>/content/<manifest及包内容>
```

页面显示配置的候选目录，成功结果显示实际落点。不修改节点配置，不安装到扫描不到的私有目录。没有合适目录时直接说明原因。

## 五项验收证据

1. **截图**：下表所有状态覆盖浅、深主题。`live-registry-*` 来自独立临时网关，真实读取线上源；其余安装状态使用可控网络夹具，不能冒充公网安装成功证据。已人工检查线上截图并修复长包名按钮换行溢出。

| 状态 | 浅色 | 深色 |
|---|---|---|
| 线上社区、真实 yanked 版本 | [截图](screenshots/live-registry-light.png) | [截图](screenshots/live-registry-dark.png) |
| 可安装版本 + yanked | [截图](screenshots/community-yanked-light.png) | [截图](screenshots/community-yanked-dark.png) |
| 源不可达 | [截图](screenshots/source-unavailable-light.png) | [截图](screenshots/source-unavailable-dark.png) |
| 安装中 | [截图](screenshots/installing-light.png) | [截图](screenshots/installing-dark.png) |
| 安装成功 | [截图](screenshots/install-success-light.png) | [截图](screenshots/install-success-dark.png) |
| 安装失败 | [截图](screenshots/install-failed-light.png) | [截图](screenshots/install-failed-dark.png) |
| 装后本地可见 | [截图](screenshots/installed-local-light.png) | [截图](screenshots/installed-local-dark.png) |
| 本地环境缺失、工作流引用 | [截图](screenshots/manager-missing-light.png) | [截图](screenshots/manager-missing-dark.png) |

2. **公网端到端**：真实请求新代理端点列出 3 个包、7 个版本，当前全部 yanked。simple-check 与 files-check 等版本使用故意全零的 code_sha256，不能通过校验。`registry-smoke/hello-20261025184133@0.0.1` 有真实身份 `d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688`，但也已 yank。未取得管理入口/凭据配置路径，所以没有临时 unyank、没有绕过校验，也没有宣称线上安装成功。见 [线上探测日志](online-probe.log) 和 [复验脚本](../../../tests/online_pack_manager_probe.py)。该探测通过真实端点、真实线上 HTTP，节点传输用测试适配器。

3. **本地发现与 workflow 引用**：新增后端测试使用真实 HTTP 注册表和真实包体验证安装、哈希、扫描，检查新端点结果后目录中可发现包，`GET /api/pack-catalog` 出现新版本，`/api/recipes/check` 不报 missing_package。另有独立临时网关 + NodeRuntime 的**真实 WebSocket** 测试，验证安装请求、节点能力协商、packs_updated 与 catalog；无网络请求 mock。见 [专项测试](../../../tests/test_pack_manager.py)、[日志](backend-pack-manager.log)。UI 自动刷新通过 11 项浏览器专项验证。

4. **yank 禁装**：线上新安装端点对 hello@0.0.1 返回 HTTP 409、`code=yanked`、中文可读原因，目录没有落盘。可控测试也验证已经安装的版本被 yank 后页面安装端点仍拒绝。按钮禁用见上表。

5. **构建与测试**：
   - [前端 build](build.log)：通过，保留原有大 chunk 提示。
   - [前端单元测试](frontend-unit.log)：49 文件、654 项通过。
   - [Pack Manager 浏览器专项](browser-pack-manager.log)：11 项通过，含双主题、窄屏、工作流画布回归。
   - [后端原全量](backend-full.log)：1589 通过、5 跳过。首次从当前工具进程树启动时两项进程保护测试失败；保留安全保护，以独立进程运行后全过。
   - [新增后端专项](backend-pack-manager.log)：5 项通过；原 pytest testpaths 只收集根 tests，因此新专项单独显式运行。
   - [全量浏览器回归](browser-full.log)：121 通过、25 失败、1 跳过，**未全绿**。失败涉及此前的画布/Inspector/端口提示/资源面板等用例。已用修改前 HEAD 导出的前端，在独立 5174 端口对照运行，25 个失败用例均在 HEAD 上失败；见 [HEAD 对照日志](browser-baseline.log)、[额外对照](browser-baseline-extra.log)。本次未扩大修改这些功能。
   - 修改的 Python 文件 Ruff 检查、git diff --check 通过。

## 交付边界与待补验收

未重启现有 HoloLab 服务；测试仅启动并关闭独立临时网关/节点。未改 website、SpacetimeGaussians 或 ColmapUtil 子模块；未 push。安装能力需用户重启更新后的网关和节点后生效。

待提供注册表管理入口或凭据配置位置，才能按授权临时 unyank hello@0.0.1、经新端点安装验证，再恢复 yank。没有要求用户再次批准安装，只缺少可用的管理访问方式。全量浏览器回归的既有失败另需处理，当前报告明确保留失败状态。
