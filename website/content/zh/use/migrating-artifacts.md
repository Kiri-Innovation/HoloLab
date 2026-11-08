---
title: 迁移产物存储
group: Full Features Intro
order: 51
---
# 迁移产物存储

更换 SSD 或给输出目录扩容时，可以用产物迁移工具复制文件、校验哈希、更新产物登记位置，并保留旧路径的可读性。它不搬运模型缓存、已安装的包、conda 环境或 Gateway 数据库；这些内容见[存储目录布局](storage-layout.md)。

## 选择迁移范围，准备维护窗口

- **更换磁盘**：迁移整个工作根目录，让后续输出写入新盘。
- **扩容**：迁移整个工作根目录，或只搬走某个产物、某个目录；局部迁移不会改变主根目录。
- **迁移到新机器**：工具处理的是本机可访问路径，不负责 SSH 传输，也不会把产物改挂到另一个节点身份下。需要让维护主机同时访问两处存储，保留节点身份与数据库的对应关系。其他状态另行备份搬运，环境重新准备，确认 Gateway 和文件服务连通后再恢复运行。若要更换节点身份，仅靠此命令无法完成。

先排空节点：结束或取消待处理、已分配、运行中及失联待处理的任务，停止新提交，维护期间不要清理产物或从外部修改文件。对数据库做一致性备份，保留源盘。目标盘应已挂载、权限正确、空间足够；源和目标使用互不包含的绝对路径，目标最好为空，不要放在源目录里面。

示例中的 `HOLOLAB_HOME` 应设置为实际数据主目录，所有尖括号占位符都要替换为当前配置。数据库必须是 Gateway 实际使用的账本，不能临时新建一个本地数据库代替。

## 启动迁移维护服务
```bash
hololab artifacts migrate serve \
  --db-path "$HOLOLAB_HOME/gateway/hololab.sqlite" \
  --node "<node-id>" --source-root "<workspace_root>" \
  --gateway http://127.0.0.1:8828
```

在一个终端保持服务运行，在同一主机的另一个终端执行 CLI。服务只监听本机回环地址的 **8830** 端口，独立于 Gateway 和节点服务。`--gateway` 用于实时更新文件服务的根目录配置，并在整根迁移时自动切换主根；地址必须指向管理该节点的 Gateway。维护主机必须同时能访问文件和权威数据库，远端执行节点上无关的本地数据库不能替代它。

服务默认在 `~/.hololab/migration.token` 创建权限为 `0600` 的令牌文件，这个 CLI 默认位置不随 `HOLOLAB_HOME` 改变。自定义时，在 `serve` 和其他每个子命令之前加 `--token-file <private-token-file>`；访问其他端点时，同样把 `--url` 放在子命令之前。不要打印或分享令牌。

## 先生成计划

以下三种范围**任选一种**：
```bash
hololab artifacts migrate plan --node "<node-id>" \
  --workspace --target-root "<new_workspace_root>"

hololab artifacts migrate plan --node "<node-id>" \
  --handle "<handle-id>" --target-root "<new_workspace_root>"

hololab artifacts migrate plan --node "<node-id>" \
  --path "<workspace_root>/selected-output" --target-root "<new_workspace_root>"
```

`--workspace` 表示整根迁移。`serve --source-root` 只允许一个根时会自动选择该根；允许多个根时，还要显式指定 `--source-root "<workspace_root>"`。按 handle 或路径选择时，工具可能把范围扩大到已登记的上层目录，以保留聚合产物的完整性。执行前检查返回的源、目标、迁移单元数、handle 数、路径别名及预检阻塞项。缺失链接、循环链接等问题应先修复，再重新生成计划。工具会在目标根下保留相对于维护服务所允许源根的目录结构。

## 执行、验证，再回收源副本

使用返回的 `migration_id`：
```bash
hololab artifacts migrate run "<migration-id>"
hololab artifacts migrate status "<migration-id>"
```

`run` 只是启动后台任务，返回不代表复制结束。持续查询 `status`，检查 `error`、`worker_running` 和 `local_progress`，直到 **`cleanup_pending`**。工具先校验文件哈希，再提交位置变更。整根迁移且指定 `--gateway` 时，会把 `workspace_root` 切到目标，并保留历史根。已登记 handle 的身份不变，路径和位置版本更新。文件系统通过原子交换在源位置留下兼容软链，原数据另行保留，供回滚使用。

检查已有产物预览、下游读取、节点实际生效的根目录配置，以及后续输出将写入的位置。在决定回滚或完成回收前，继续保持节点排空：新增写入可能让回滚校验不再通过。

```bash
hololab artifacts migrate finalize "<migration-id>" --delete-source
hololab artifacts migrate status "<migration-id>"
```

完成操作会再次校验目标，然后按明确授权删除保留的源副本，进入 **`completed`**。不加 `--delete-source` 则仍保留副本。旧路径软链继续存在；移除旧盘后，也要确保软链所在的父路径仍然可用。**源副本开始回收后就不能回滚。**

## 中断恢复与回滚

```bash
hololab artifacts migrate status "<migration-id>"
hololab artifacts migrate resume "<migration-id>"
hololab artifacts migrate cancel "<migration-id>"
hololab artifacts migrate rollback "<migration-id>"
```

客户端断开或超时后，先查状态，服务端可能还在工作。服务中断后，用相同的数据库、节点、允许根目录和令牌重启，再恢复原迁移；不要另建计划覆盖同一批文件。`cancel` 协作停止尚未提交的操作，保留已准备的目标文件供检查，不会撤销已提交迁移。执行恢复操作前应等后台工作停止。

回滚适用于已提交、且仍保留源副本的迁移。工具校验副本后恢复源目录和登记路径；连接 Gateway 的整根迁移还会把主根切回原处。文件或 handle 已被改动时，工具可能拒绝回滚，应根据错误排查，不要手改账本。

## 常见问题

- **目标盘满了**：扩充空间或删除无关文件，再查询状态并恢复；不要删除迁移日志或源备份。
- **迁移后环境版本不符**：迁移不修改 Python 依赖，应检查执行节点的[包环境](../contributor/pack-environments.md)，按声明修复依赖。
- **有多个节点**：每个节点分别规划、分别迁移。搬走一个根目录，不会迁移其他节点的数据，也不会改写节点身份。

迁移结束、后台任务停止且操作已处理完毕后，可以关闭服务，也可以保留供下次维护使用。迁移任务是一次有记录的操作；旧路径软链不依赖维护服务常驻。
