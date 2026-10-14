---
title: 产物与工作空间
group: 数据与存储
order: 10
---
# Artifact 与工作空间布局

HoloLab 执行的每个 Job 都会将文件写入计算设备的**工作空间根目录**。本文介绍磁盘上的目录布局、如何更换根目录（例如迁移到数据盘），以及 Artifact 分类和生命周期策略的后续规划。

## 目录布局

计算设备的工作空间根目录默认为 `~/.hololab/node/workspace/`。Workflow 的运行数据按目录组织，目录内再按 Job 划分：

```
{workspace_root}/
├── inputs/                       # cross-node handle staging (fetched dirs live here)
│   └── {handle_id}/…
└── w/
    └── {workflow_id}/
        └── j/
            └── {job_id}/
                ├── model_dir/       # named after the pack's output ports
                ├── message.txt      # arbitrary files produced by the shell command
                └── …
```

- **`{workflow_id}`** 将同一张图的各次 Run 归到同一个磁盘目录中。临时触发单个 Pack（POST `/api/jobs/run`）时，会生成一个 `adhoc-{uuid}` 标识。
- **`{job_id}`** 是一次 Node 执行的 UUID。manifest 中声明的每个端口都会对应此目录下的一个子目录或文件。
- **`inputs/{handle_id}`** 用于存放从其他计算设备获取的输入。下游 Job 在另一台机器上运行、经由 Gateway 代理拉取上游 Handle 时，解包后的数据就放在这里。

Gateway 在 `handles` 表中记录**绝对路径**。预览代理去掉工作空间根目录前缀，得到 `/proxy/{node_id}/{sub}` URL；计算设备的文件服务器再根据已知的根目录解析这个子路径。

## Handle 的磁盘存储形式

`handles.storage` 列提供传输提示，决定文件服务器如何为其他计算设备上的使用方传输 Handle 对应的数据：

- **`storage: dir`**（默认）—— Handle 路径指向目录；跨设备获取时，将目录树打包成 tar 归档。所有输出重建结果的 Pack 都使用此形式，包括 COLMAP 数据包、模型目录和 arrayed<T> Handle。
- **`storage: file`** —— Handle 路径指向单个文件；跨设备获取只需一次 HTTP GET。

### `int` 标量 Handle

arrayed<T> 类型系统引入了这种 Handle，让 `array-length` 等 Node 可以将计数传给 `arrayfy` 和 `get-index`。

- `storage: file`，`tags: [int]`，内容为纯文本十进制整数（允许末尾有空白或换行）
- 生产方 shell：`echo N > "{{ outputs.n }}"`
- 使用方 shell：`n=$(cat "{{ inputs.n }}")`
- `GET /api/handles/{id}/summary` 返回 `{"kind": "scalar-int", "fields": {"value": <int>}}`，预览抽屉无需下载原始数据即可显示数值；解析失败时，通过 `fields.error` 和 `fields.raw` 返回错误及原始内容。

### `arrayed<T>` Handle

arrayed<T> Handle 使用 `storage: dir`。该目录下名称不以点开头的直接子项就是类型为 T 的元素，可以是图像文件、数据包目录、嵌套集合或有效的符号链接。元素 ID 取自排序后的条目名称。框架为 arrayable Pack 执行 Fan-out 时，将各分片的输出写入 `{parent_workspace}/{port}/{element_id}/`；聚合后的父 Handle 指向 `{parent_workspace}/{port}/`。参见 [pack-spec.md#arrayed-and-arrayable](pack-spec.md#arrayed-and-arrayable)。

### 严格图像约定与测量

`image` 表示一个可完整解码的单帧文件（`storage: file`）。图像集合使用 `arrayed: true`、`storage: dir`，以及按从外到内顺序排列的 `dim_labels`；不存在隐式的 `frames/` 包装层，也没有表示图像内容数量的维度。已移除的类型标签 `image_sequence` 和 `frame_sequence` 会被拒绝，但端口仍可使用这两个名称。每份 manifest 描述一次调用；Fan-out 在外层增加一层集合。系统会在执行或发布前验证每个图像叶项。开发者需要准确声明算法对输入的要求。

`runtime_shape` 报告显式维度和数据包内部计数，并附带 unknown/sampled/complete 证据状态。对 COLMAP 数据包进行采样并不等于完整验证其契约。Pack 迁移不会重写已存储的旧 Artifact；旧布局可能仍能预览，但必须满足当前契约才能复用。

## 删除与预览抽屉

`DELETE /api/artifacts/{handle_id}`（由 Artifacts 面板或 API 调用）会删除磁盘文件，**并且**在 Handle 记录上写入 `deleted_ts`。记录本身保留，以维持完整的历史与沿袭关系。`GET /api/handles/{id}` 返回带有 `deleted_ts` 的记录，画布预览抽屉据此显示相应状态：

- `deleted_ts !== null` → 抽屉显示 **“产物已被清理”** 占位提示和 **Run this node** 按钮（使用现有的 `POST /api/workflows/{wid}/dispatch/{gnid}` Continue/Fork 端点）。此时不会发起 `<video>` / `<img>` 请求，因此不会闪现损坏的画面，也不会对代理 URL 发起导致 404 的请求。
- 从未生成过 Handle（该画布 Node 尚无 Run）→ 抽屉显示 **“尚未运行”** 占位提示和相同的 Run 按钮。
- 只读 Snapshot 视图（`SnapshotCanvas`）隐藏 Run 按钮。从已经冻结的历史 Snapshot 直接重新运行并没有明确的语义；操作者需要返回草稿，触发新的 Run。

如果绕过 API，在文件系统中直接用 `rm` 删除文件，仍保持 `deleted_ts = null`。抽屉会继续尝试渲染预览，浏览器则显示原生的“无来源”状态。这是目前仍存在的边界情况；主要操作路径（通过 Artifacts 面板删除）已得到处理。

## 按引用计数删除 Run

Run History 面板的每一行都提供右键操作 **“删除这个 run 及其产物”**。对应的两个端点是：

* `GET /api/snapshots/{sid}/deletion-preview` —— 只读预览删除影响。
* `DELETE /api/snapshots/{sid}` —— 执行删除。

确认弹窗先请求影响预览，再显示具体数量：**“K 个产物将被删除 · 保留 N 个 (仍被别的 run 引用) · 释放约 B 字节.”** 只有操作者点击“确认删除”后，才会执行删除。

### 删除规则

每个 Artifact `H` 只由一个 Job `J` 生成。在 V8 以沿袭关系为核心的模型（`docs/workflow-schema.md`）中，一个 Job 可以归属于**多个** Snapshot：Continue 通过新增归属关系扩展 Snapshot；Fork 将父 Snapshot 中未发生分叉的归属关系继承到子 Snapshot。`snapshot_jobs` 表承载这一多对多关系。

删除 Snapshot `S` 时，对归属于 `S` 的每个 Job `J` 按以下规则处理：

| 情况 | 定义 | 对磁盘的影响 |
|---|---|---|
| **独占** | `J` 在 `snapshot_jobs` 中只有 `S` 这一条归属记录 | 通过生成这些数据的计算设备（`artifact_delete_req`）物理删除 `J` 的 Handle 对应的数据，并写入删除标记（设置 `deleted_ts`）。同时清除 `J` 本身及其 `job_events` / `job_logs`（FK CASCADE）。 |
| **共享** | `J` 在 `snapshot_jobs` 中仍有至少一条其他归属记录 | 磁盘内容不变。只移除 `S` 自己的归属记录（由 `snapshots` 触发 FK CASCADE）。Handle 记录保持 `deleted_ts = NULL`，`J` 也保留。 |

这条规则由 `hololab/gateway/snapshot_delete.py` 实现。`tests/test_snapshot_delete.py::test_delete_shared_artifact_keeps_file_on_disk` 验证两个 Snapshot 共享一个 Artifact 时，删除其中一个仍保留文件；`::test_delete_last_reference_removes_file_and_tombstones` 验证删除最后一个引用时，确实会删除文件并写入删除标记。

### 拒绝删除的情况

如果归属于 `S` 的任一 Job 仍处于非终态（`pending` / `assigned` / `running`），DELETE 端点会返回 **409**，详情载荷为 `{message, live_jobs}`。确认弹窗会列出这些仍在进行的 Job，并禁用“确认删除”按钮；操作者必须先取消 Run。目前对离线计算设备上的 Job 进行取消还不够可靠，因此系统不会尝试自动取消，而是让操作者明确选择。

### 幂等性与边界情况

* **两个标签页同时删除同一项**：第二次 `DELETE` 返回 `200 {state: "gone"}`，而非错误。前端只需刷新列表。
* **预览不存在的 Snapshot**：返回 `404`，以区分“存在但为空”和“已经不存在”。
* **生成数据的计算设备离线**：删除仍然成功；每个受影响的 Handle 都会写入删除标记（设置 `deleted_ts`），Artifacts 页面因此隐藏对应行。设备重新上线后，磁盘上的数据仍会遗留；未来计划通过 `hololab workspace prune` 清理（见下文）。
* **路径位于当前工作空间根目录之外**（更换根目录后）：与设备离线时相同，只写入删除标记，删除请求仍然成功。
* **删除正在查看的 Run**：面板通知删除成功后，`App.tsx` 会清空 `viewingSnapshot`，让画布返回草稿。
* **Fork 子 Snapshot 的 `snapshots.parent_snapshot_id`**：删除父 Snapshot 时，将该字段置空。该列没有外键约束，因此在同一事务中手动处理，避免 UI 显示“forked from &lt;deleted-uuid&gt;”这样的失效链接。

## 配置工作空间根目录

### 优先级

1. **CLI 参数**：在 `hololab start` 或 `hololab start node` 上指定 `--workspace-root /path/to/root`，优先级最高；必须使用绝对路径。
2. **配置文件**：在 `~/.hololab/node/config.yaml`（或 `--config` 指定的位置）中设置 `workspace_root: /path/to/root`。
3. **默认值**：`~/.hololab/node/workspace/`（或 `$XDG_DATA_HOME/hololab/node/workspace`；Windows 上为 `%APPDATA%\hololab\node\workspace`）。前两项均未设置时使用。

### 从 UI 修改（即时生效，无需重启）

在 Compute nodes 面板中，点击计算设备旁的 ⚙，打开 **Node settings** 抽屉：

- 修改 **Workspace root**（必须为绝对路径）。
- 修改 **Legacy workspace roots**（见下文）。
- 修改 **Node name**（显示别名）或 **Advertised URL**。

应用修改时，计算设备会：

- 验证各个字段。
- 将合并后的配置保存到 `~/.hololab/node/config.yaml`。
- 如果根目录列表发生变化，热重启文件服务器（重新绑定时会有不到一秒的短暂间隔）。
- 返回实际生效的配置；失败信息会直接显示在抽屉内。

抽屉实际调用 `PATCH /api/nodes/{node_id}/config`，Gateway 再通过 WebSocket 将其作为 `node_config_set_req` 转发给计算设备。正在运行的子进程不受影响：新 Job 使用新根目录，现有 Job 继续在启动时指定的路径下执行。

## 旧工作空间根目录

更换主根目录后，**旧 Artifact 仍留在原处**。HoloLab 不会自动迁移目录，因为是否要将数 GB 的 Gaussian Splatting 输出移出 SSD，应由你自行决定。

将旧路径加入 `legacy_workspace_roots`，计算设备的文件服务器就能继续以只读方式提供这些 Artifact：

```yaml
workspace_root: /data/hololab/workspace
legacy_workspace_roots:
  - /root/.hololab/node/workspace          # old default location
```

计算设备的文件服务器按顺序搜索根目录：先主根目录，再旧根目录。Gateway 计算代理 URL 时也会做对应的前缀剥离，因此已有 Handle 仍可通过 `/proxy` 路由访问，无需修改前端代码。

旧根目录的**设计用途是只读访问**，不会再向其中写入数据。如果要把部分旧 Artifact 移到新根目录，可以使用 `mv`；不过 Gateway 保存的路径仍指向旧位置，因此在确认没有任何 Handle 再引用这些位置之前，应保留旧根目录配置。

## 后续规划（TODO）

接下来计划引入 **Artifact 生命周期策略**。目前，计算设备生成的内容都会一直保留在 `workspace_root` 下。后续设计包括：

- **manifest 中的 Artifact 分类**：输出声明类别（`intermediate` / `deliverable` / `debug`），并映射到保留策略。中间结果在下游使用方完成后回收；交付结果长期保留；调试 Artifact 保留 N 天。
- **由 Snapshot 触发保留**：用户加星标的 Snapshot，或通过 `hololab://run/...` 引用的 Snapshot，其输出不参与垃圾回收，直到引用消失。
- **设备外的对象存储**（可选）：为交付结果配置 `object_store: s3://…`，让本地根目录只保留中间结果和一份近期副本。Handle 记录已经采用 `node_id → path` 抽象，扩展到 `node_id → store_uri` 可以在现有设计上增量实现。
- **`hololab workspace prune` CLI**：按 Workflow、类别和存放时间分组，交互式报告磁盘内容，并通过 `--dry-run` 模式给出删除建议。目前还没有原地迁移命令；对 v1 而言，上述“将旧路径加入旧根目录列表”的流程更简单、安全。

以上功能均未实现。这里记录设计方向，便于后续贡献者接着推进，无需从头梳理。

## 从默认目录切换到数据盘

```bash
# 1. Pick a target root on the data disk.
sudo install -d -m 755 -o "$USER" /data/hololab/workspace

# 2. Add it to the node config.yaml BEFORE moving anything.
cat >>"$HOME/.hololab/node/config.yaml" <<'EOF'
workspace_root: /data/hololab/workspace
legacy_workspace_roots:
  - /root/.hololab/node/workspace
EOF

# 3. Restart the node (or the all-in-one `hololab start`).
#    New jobs land at /data/hololab/workspace; old handles keep resolving
#    via the legacy root.
kill -INT "$(pgrep -f 'hololab start')" && hololab start &
```

通过 CLI 验证：

```bash
hololab status
# → node …  kiri4090  packs=6  gpu=…
#           workspace /data/hololab/workspace
#           legacy    /root/.hololab/node/workspace
```

也可以通过 UI 验证：打开 Compute nodes 面板 → ⚙，抽屉中会显示这两个字段当前生效的值。
