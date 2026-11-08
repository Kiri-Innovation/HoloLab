---
title: HoloLab 的文件存在哪里
group: Full Features Intro
order: 52
---
# HoloLab 的文件存在哪里

HoloLab 把用户数据放在应用安装目录之外。设置 `HOLOLAB_HOME` 可以指定数据主目录；未设置时，Unix 优先使用已配置的 `$XDG_DATA_HOME/hololab`，否则使用 `~/.hololab`；Windows 在可用时使用 `%APPDATA%/hololab`。下文用 `$HOLOLAB_HOME` 统称实际解析出的数据主目录。Gateway 与执行节点位于不同机器时，各自使用本机的数据目录。

## 默认目录全景
```text
$HOLOLAB_HOME/
├── gateway/
│   └── hololab.sqlite
└── node/
    ├── config.yaml
    ├── weights/
    ├── artifacts/
    └── installed_packs/
```

这是一套默认布局，不要求所有内容放在同一块盘上。节点配置可以分别指定 `workspace_root`、`cache_root` 或各库的 `cache_dirs`、包扫描目录以及环境映射。

| 位置 | 谁写、谁读 | 能不能删 |
| --- | --- | --- |
| `node/weights/` | setup 脚本和模型库下载公共资源，setup 与任务读取缓存 | 只在空闲时清理可重新下载的内容。离线资源或手动提供的权重未必能恢复，应另行保留。 |
| `node/artifacts/` | 任务写输出，下游任务、文件服务和预览读取 | 不是临时缓存。使用带依赖、引用检查的产物清理；手删文件会留下失效记录，破坏历史读取。 |
| `node/installed_packs/` | `pack install` 解压已校验的注册表归档，扫描、setup 和执行读取 | 确认所需工作流和运行记录不再依赖后才移除。需要时重装同一发布版本，不要原地修改已校验的源码。 |
| `gateway/hololab.sqlite` | Gateway 持有权威账本，通过数据库写服务写入；维护工具使用受支持的写入通道 | 不能当缓存删除。应做一致性备份，必要时包含 SQLite WAL 状态；不要只复制运行中的主文件，也不要手改数据行。 |
| `node/config.yaml` | CLI 和配置 API 保存设置，节点启动及运行时读取 | 应保留，里面记录节点身份、路径和环境映射。搬机前备份。 |
| conda 环境目录 | `pack setup` 和环境工具安装依赖，任务从这里启动解释器 | 只有声明完整且依赖来源可用时才便于重建。不要删除正在使用或共用的环境，操作前记录映射。 |

仓库里的 `community_packs/` 保存包源码，`installed_packs/` 保存发布归档的本地安装副本。对应发布版本的代码应一致，但编辑仓库不会同步修改已安装副本。模型文件和可变的 setup 执行凭据不属于包源码。

## 环境不在产物目录里

conda 通常在已配置的环境目录创建命名环境，一般使用 `envs_dirs` 中第一个可写位置；实际配置可用 `conda info --json` 查看。HoloLab 在节点的 `envs` 中记录逻辑环境名到实际 prefix 的映射，已有映射也可能指向其他位置。迁移产物目录不会搬动这些环境，详见[包环境与依赖](../contributor/pack-environments.md)。

## workspace_root 的含义

`workspace_root` 是节点写入新输出的主根，也是文件服务的主要路径命名空间，默认指向 `node/artifacts/`。它不是仓库目录、包源码根或权重缓存。`legacy_workspace_roots` 把历史根纳入可读范围，让已登记路径继续访问；只加配置不会复制文件，也不会改变后续输出位置。

连接 Gateway 的整根[产物迁移](migrating-artifacts.md)会更新主根并保留历史根；按 handle 或路径迁移则不切换主根。源位置会留下指向新位置的软链，因此历史绝对路径仍能解析，无须逐份改写已保存工作流。软链所在的父路径必须可用；历史根配置无法补救丢失的挂载或断开的软链。

迁移服务监听回环地址的 8830 端口，操作账本和本地存储，维护结束后可以关闭。正常任务和软链不依赖它常驻。保留的源副本支持回滚，但明确执行源回收后就不再支持。

## 按数据职责安排备份

将账本、节点配置和仍需使用的产物一并保留。记录精确包身份和环境映射，便于重新安装代码、重建环境。权重是共享资源，不是每次运行的输出；下载昂贵、来源不可用或授权独立时，应保留副本。仅修改 `HOLOLAB_HOME` 不会迁移文件，也不会改写配置和历史中保存的路径。
