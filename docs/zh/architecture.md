---
title: HoloLab 架构
group: 系统设计
order: 20
---
# HoloLab 架构

HoloLab 将浏览器界面、Workflow 协调和算法执行分开。算法在配置好的环境中以子进程运行；HoloLab 不会将算法的 Python 模块导入自身运行时。

## 组件

| 组件 | 职责 |
| --- | --- |
| 前端 | 基于 React 和 TypeScript 的画布、Workflow 编辑、任务状态、日志和预览。 |
| Gateway | HTTP 与 WebSocket API、Workflow 验证、调度、持久化和 Handle 查询。 |
| 计算设备 | Pack 发现、环境解析、子进程执行和文件服务。 |

`hololab start` 同时启动 Gateway 和本地计算设备，并启动文件服务器子进程。独立的 Gateway 与计算设备命令支持多机部署。Workflow 中的每个算法 Node 都会分配给提供相应 Pack 的计算设备。

## 连接拓扑

计算设备主动与 Gateway 建立持久 WebSocket 连接，注册自身资源，并上报心跳、任务状态、日志和 Handle。Gateway 通过这条连接下发任务和取消请求。浏览器使用 Gateway 的 HTTP 和 WebSocket 端点。

建立出站控制连接，并不意味着文件服务器也可以被访问。跨设备传输数据时，需要配置生产方对外公布的文件服务器 URL，并从使用方设备测试其可达性。不能仅凭注册成功，就认定 NAT 后的计算设备能够支持所有数据操作。

## 草稿、Snapshot 与 Job

草稿是可编辑的 Workflow。前端会自动保存编辑并显示保存状态。提交 Workflow 前，应等待保存成功。已提交的 Job 使用记录下来的配置，之后对草稿的修改不会改变正在运行的子进程。

Snapshot 记录 Workflow 和 Job 的沿袭关系。Job 与 Snapshot 之间是多对多的归属关系，因此后续 Run 可以复用已有结果。单 Node 派发可以继续使用兼容的 Snapshot，也可以在沿袭关系要求时创建分叉。记录结构及 Continue/Fork 语义参见 [Workflow Schema](workflow-schema.md)。

视图贴片用于查看输出，不创建算法 Job，也不构成执行依赖。

## 执行边界

Pack 声明输入、输出、参数、环境要求和 `exec.shell` 模板。计算设备根据自身配置解析逻辑环境名，然后以子进程启动命令。

计算设备可以缓存环境快照，避免每个分片都重复启动 conda；回退路径使用 `conda run`。如果修改影响了环境激活后的环境变量，应在合适的时间重启计算设备，以刷新环境缓存。这属于运维操作，不是 Workflow 编辑的一部分。

manifest 描述算法的一次调用。对于 arrayable Pack，Node 的 Fan-out 会增加一层外层集合。每个分片从映射输入中消费一个元素；广播输入则保留声明的完整值。收集输出也会增加一层外层集合。参见 [Pack 规范](pack-spec.md)。

## Data plane

Job 向 Gateway 注册文件或目录的 Handle。Handle 记录生成数据的计算设备及路径；算法模板接收的是本地路径。

解析输入时，计算设备可以直接使用提供的本地路径，也可以请求 Gateway 定位 Handle。属于请求方设备的 Handle 会解析为本地路径。如果数据来自远端设备，Gateway 可以根据生产方对外公布的 URL 返回 HTTP URL。使用方将文件下载到输入暂存区，或下载目录归档并解包到该区域。如果没有对外公布的 URL，就会报错；运行时不会凭空生成可达的路由。

浏览器预览使用 Gateway 代理路由。因此，“所有大文件都不经过 Gateway”并不能概括整个系统：需要区分计算设备之间的输入传输与浏览器的预览、下载流量。

输出路径根据声明可以是文件，也可以是目录。存储、迁移和按引用计数删除的规则参见 [Artifact 与工作空间布局](artifacts.md)。其中列为后续规划的自动生命周期清理，并非当前已有功能。

## 类型与观测

manifest 声明描述所需数据；规范化端口类型描述调用视图和传输视图；运行时观测描述实际测量过的 Handle。测量结果不能替代声明，也不能证明兼容性。

验证会检查实际生效的类型结构，包括集合层级及适用的轴标签规则。图像数据需要满足严格的单文件契约或显式集合契约。参见 [端口类型 IR](type-ir.md)。

## 持久化与恢复

Gateway 将 Workflow、Snapshot、Job 和 Handle 元数据持久化到 SQLite。运行时连接丢失与算法失败是两种不同情况。设备断开连接后，任务可能仍在运行；应等待状态核对完成，再决定是否重复提交。如果恢复失败，应先检查设备和残留进程，再重新运行。

任务记录区分取消、失败和中断。取消记录并不标识是哪位操作者发出了停止指令。

## 安装与部署边界

公开的快速开始文档介绍从源码安装。仓库也包含用于打包 Python 运行时和前端的构建工具，但仅凭这些工具的存在，不能认定所有平台都有已发布、可下载的版本。

算法依赖、CUDA、模型权重和数据集仍需由操作者准备。多机部署还需要可达的控制通道、数据通道，以及正确的环境映射。

## 协议

每个 WebSocket 帧都是一个 JSON 封装。例如：

```json
{
  "v": 1,
  "id": "message-uuid",
  "kind": "heartbeat",
  "payload": { "running_jobs": [], "gpu_util": null },
  "ts": 1735000000.0
}
```

`v` 是协商后的协议版本，`id` 标识消息，`kind` 选择 Pydantic 载荷模型，`ts` 是发送方的墙上时钟时间。载荷中的 Job 或 Handle ID 等标识符用于关联操作；封装层 ID 并不构成通用的请求/响应契约。注册表和解码器位于 `hololab/protocol/envelope.py`，载荷模型位于 `messages.py`。

注册时会公布 `v_min` 和 `v_max`。`register_ok` 返回 `node_id`、`session_id`、`protocol_v`，以及可选的、由服务端签发的 `node_token`；计算设备保存自身身份和令牌，供重连使用。注册错误区分版本、认证和协议问题。

| 方向 | 消息类别 |
| --- | --- |
| 计算设备 → Gateway | `register`、`heartbeat`、`node_metrics`、`packs_updated`、Job 确认/进度/日志/完成/失败、`handle_register`、`handle_locate_req`。 |
| Gateway → 计算设备 | 注册响应、`job_assign`、`job_cancel`、Handle 查询响应、设备配置请求、Handle 检查及 Artifact 删除请求。 |
| Gateway → 前端 | `job_update`、`log_chunk`、`preview_ready`、设备上线/离线、`workflow_updated`。 |

`JobAssign` 标识 Workflow、Job、算法名称与版本、参数和输入 Handle。可选的输入路径及分片字段支持直接使用本地路径、Fan-out 和批处理。`HandleLocateResp` 携带 `local_path` 或 `http_url`、存储形式以及未找到标志。即使远端 Handle 已知，也仍可能缺少可达的 URL。

`JobFail` 包含 Job ID、原因，以及可选的退出码、日志尾部和消息。原因分为 `user_error`、`algo_error`、`system_error`、`oom` 和 `cancelled`。这些分类并不意味着系统会自动重试。

计算设备的心跳间隔为 15 秒，Gateway 判断存活的阈值为 45 秒。重连退避时间上限为 30 秒。重连后会核对设备上报的 Job 状态，而不是直接认定断开连接的任务已经失败。

## 持久化实现

`hololab/persistence/db.py` 使用 WAL 模式的 SQLite，并启用外键、串行写入队列和一个读取连接。队列中的写入可以合并到一个事务中；每个操作的保存点用于隔离批次内的失败。这样既能保持写入顺序，也不会开启相互竞争的写事务。

`jobs` 记录当前状态，事件和日志记录任务历史。Workflow、Snapshot、`snapshot_jobs`、计算设备、Pack 和 Handle 共同保留执行上下文。只向前执行的迁移位于 `hololab/persistence/migrations/`。用户数据保存在代码检出目录之外，通常位于 `~/.hololab/`，也可由平台默认值或配置覆盖。移动已存储文件之前，请先查阅工作空间参考文档。

## Execution environment and cancellation

`hololab.node.env_cache.warmup_env_cache` 在计算设备启动时捕获各个已配置环境。Executor 使用缓存时，以捕获的显式环境运行 `bash -c`；回退时，在清理后的环境中使用 `conda run -p <prefix> --no-capture-output bash -lc`。两条路径都不会导入算法代码，也不会通过修改父进程环境来切换 Job。

禁用 `use_env_cache` 或无法捕获某个环境快照时，会使用回退路径。修改激活环境变量后需要刷新缓存；仅安装软件包文件与修改已捕获的环境变量是两回事。不要假定缓存为所有软件包内容都创建了快照。

在 POSIX 系统上，取消操作先向进程组发送 SIGTERM，最多等待 30 秒，再升级为 SIGKILL。这种方式可以触达 shell 的子进程。发送任一信号之前，Executor 都会检查进程组是否仍属于运行时，避免误伤复用 PID 的进程。当前 Windows 分支调用进程终止；基于 CTRL_BREAK_EVENT 的进程组取消尚未实现。

机器配置将逻辑 `runtime.env` 名称映射到实际的 conda 前缀路径，同时提供 `conda_bin`、`pack_dirs`、工作空间根目录和对外公布的 URL。Pack 标识与执行声明仍保留在 manifest 中。源发现与验证参见 [编写 Pack](writing-a-pack.md)。

## 打包实现

仓库中的构建工具可以将独立解释器、Python 包和已构建的前端组装在一起。wheel 通过构建配置包含 `hololab/frontend/dist`，Gateway 负责提供这些资源。便携启动器工具通过 `build/sh-trampoline.tmpl` 定位同目录下的解释器，而不是保留构建机器上的绝对 shebang 路径。

`build/` 下的构建脚本在各自的目标平台上运行。Pack 环境、CUDA 和算法依赖仍在产品运行时之外。用户状态保存在安装目录之外。构建出的安装包应在干净的目标机器上测试；存在构建工具，并不能证明所有平台的版本都已发布或通过验证。

## 与 ComfyUI 的对照

如果你熟悉 ComfyUI 或其他 Node 编辑器，可以从以下边界理解 HoloLab。这些内容描述的是 HoloLab 自身的契约，不对其他工具的扩展能力或部署方式作假设。

| 设计关注点 | HoloLab 契约 |
| --- | --- |
| 执行 | 算法命令在独立子进程中运行。 |
| 依赖 | 逻辑运行环境映射到各设备上的 conda 前缀路径。 |
| 数据交换 | Handle 解析为文件或目录，而非进程内共享的张量。 |
| 计算位置 | Gateway 协调显式指定的计算设备。 |
| 复用 | 输出标记和匹配的输入沿袭关系支持复用；参见 Pack 的幂等性规则。 |
| 长时间运行的 Job | 任务状态、心跳、日志和取消操作独立于画布编辑进行跟踪。 |
| 扩展单元 | manifest 描述算法的一次调用及其声明的输入和输出。 |

Node 图界面可能很熟悉，但上述边界决定了如何拆分算法、准备依赖，以及排查失败的 Run。
