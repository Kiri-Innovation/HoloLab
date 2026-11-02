---
title: 从源码运行并迭代开发
group: 开发调试与 PR
order: 35
---
# 从源码运行并迭代开发

你可能正在用 HoloLab 完成一项真实任务，同时发现一个问题，想立即修好并继续运行。此时不要只安装发行版：发行版是已经打包的静态产物，改工作区里的源码不会改变它。用源码启动 HoloLab，改动才能进入下一次界面加载或下一次任务。

本文只说明迭代时怎样启动、改动何时生效。首次准备 Python、Node.js、子模块、虚拟环境和 Pack 环境，请先完成[安装和运行](/docs/quick-start)。

## 用开发模式启动

完成快速开始中的安装后，在仓库根目录打开两个终端：

```bash
# 终端 1：Gateway 自动重载；本机节点作为独立进程启动
.venv-runtime/bin/hololab dev

# 终端 2：前端热更新
cd hololab/frontend
npm run dev
```

浏览器打开开发服务器显示的地址，默认是 [http://127.0.0.1:5173](http://127.0.0.1:5173)，不是 Gateway 的 `:8828`。Vite 会把 `/api`、`/proxy` 和 `/ws` 转发给 Gateway；`hololab dev` 也会让 `:8828` 不再提供可能过期的 `frontend/dist`。

如果你要验证发布式界面而不是前端热更新，使用快速开始中的 `hololab start`，在 `hololab/frontend` 执行 `npm run build`，再访问 `:8828`。下面的速查表说明每种改动是否还要重启。

## 改动后怎样生效

| 改动位置 | 正在使用什么启动方式 | 生效方式 | 对正在运行的 Job |
| --- | --- | --- | --- |
| `hololab/frontend/src/` 中的 React/UI | `npm run dev` | Vite HMR；若该模块不能热替换，刷新 `:5173`。不重启 Gateway。 | 不影响 |
| 前端构建产物 `frontend/dist` | `hololab start` 在 `:8828` 提供界面 | 执行 `npm run build` 后刷新浏览器；建议强制刷新。StaticFiles 每次请求从磁盘查找文件，不必重启 Gateway。 | 不影响 |
| 新增或修改 `/api` 路由、Gateway Python 代码 | `hololab dev` | Gateway 的 reloader 会重启；等待它重新监听后重试请求。 | Gateway 重启期间请求会断开；节点会自动重连，已启动的 Job 不会被 Gateway 重启终止。 |
| 新增或修改 `/api` 路由、Gateway Python 代码 | `hololab start` 或独立 `hololab start gateway` | 重启 Gateway。路由在应用创建时用 `include_router` 注册，不能靠浏览器刷新加入。 | 同上 |
| Pack 的 `manifest.yaml`、包内实现文件或 Pack viewer 资源 | 任意正常节点 | 节点 watcher 重新扫描并发送 `packs_updated`；Gateway 转发后界面更新，无需重启节点。也可点击 **Packs** 面板或包管理页的 **重新扫描包**，它会调用 `POST /api/packs/reload`；接口可用 `node_id` 指定一个节点，省略则重扫全部在线节点。 | 不影响已启动的 Job；只影响之后启动的 Job。 |
| fileserver Python 代码 | 任意启动方式 | 重启承载该节点的进程；一体模式就是重启 HoloLab。当前没有 fileserver 代码的自动热重启入口。 | 不会由 fileserver 重启终止已启动的 Job；缩略图、预览和工作空间文件服务会短暂不可用。 |
| 节点侧 Python 代码（调度、扫描、执行等） | 任意启动方式 | 重启该节点；一体模式下重启 HoloLab。`hololab dev` 只自动重载 Gateway，不自动重载节点。 | 不要预期已启动的 Job 采用新代码；改动用于后续 Job。 |

前端构建的 JS/CSS 文件名带内容哈希。已经打开的页面仍引用旧文件名，因此即使新构建已经落盘，也要刷新页面；缓存异常时用浏览器强制刷新更稳。

Pack watcher 会完整重扫并重新发布库存。若 manifest 不能通过 schema 校验，该 Pack 会被忽略，节点日志会记录 `pack manifest load failed` 和原因；先修复 manifest 再重扫。外部 Pack 应声明 conda runtime；`runtime.mode: host` 与 `exec.python` 只允许 HoloLab 内置的 bundled Python 包，外部 Pack 这样写会在 schema 校验时失败。

## 哪些改动不会追溯到已经启动的任务

一次运行在启动时会取得自己的 Pack、参数和执行快照。正在跑的子进程不会因为你保存了 Pack 或节点源码而被替换；继续观察它的日志和产物，验证改动时再启动一个新的 Job。这样可以一边跑长任务，一边修下一次执行要用的问题。

fileserver 是节点拉起的独立子进程，专门处理 ffmpeg 较重的缩略图和预览请求，避免占用调度器事件循环。配置 `workspace_root`、`legacy_workspace_roots` 或 `pack_dirs` 时，节点会调用 fileserver 重启逻辑；其中正在运行的 Job 保有启动时的写入路径，不会被打断。除此以外，修改 fileserver 源码后没有单独的热重载按钮，重启节点才会载入新代码。

如果刚在 conda 环境里安装或升级依赖，也重启节点。节点默认缓存已激活环境；不重启时，新包不一定会出现在后续子进程的环境中。

## 用 screen 起停并确认清理干净

多台开发机上约定把常驻会话命名为 `hololab`。进入会话后正常按 Ctrl-C 结束，再从仓库根目录重新执行启动命令；按 `Ctrl-A`、`D` 可脱离 screen 而不停止它：

```bash
screen -r hololab
# 在会话内正常结束并重新启动，例如：
.venv-runtime/bin/hololab dev
```

不要用 `kill -9` 结束节点：它不会执行节点的收尾逻辑。正常的 HUP、TERM 和 INT 会取消节点运行时，再由它向独立 fileserver 发送 TERM、等待并在必要时回收；这样下一次启动不会因 `8829` 被占用而失败。

重启后可在另一个终端检查：

```bash
.venv-runtime/bin/hololab status
ps -ef | rg '[h]ololab.node.fileserver_main'
ss -ltnp 'sport = :8829'
```

`hololab status` 应能访问默认 Gateway `8828`；应只看到本次节点启动的一个 fileserver，并且 `8829` 的监听者应是它。若重启前后出现旧 fileserver 或端口仍被占用，先回到原来的 screen 会话正常停止它，再查明遗留进程，而不是直接用 `kill -9`。

默认情况下 `8828` 是 Gateway，`8829` 是节点 fileserver；若启动时改了 Gateway 端口，以实际启动输出和节点配置为准。
