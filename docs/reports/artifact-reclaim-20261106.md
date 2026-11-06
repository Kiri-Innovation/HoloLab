# 迁移源回收尝试与两盘占用（2026-11-06）

**回收被官方安全检查拒绝，未删除任何源备份或 SSD2 数据，本次释放 0 字节。**
用户已批准删除源备份；阻塞原因是节点仍有运行和排队作业，不是缺少删除授权。

执行时间：2026-11-06T11:01:27.822613+00:00 至 2026-11-06T11:01:28.658211+00:00。

## 命令与结果

已先读取任务书、查询 status 并用 finalize --help 确认参数，然后执行：

```sh
.venv-runtime/bin/hololab artifacts migrate finalize fa13e786-b60c-4377-92ba-80a6101e9039 --delete-source
```

退出码 2；HTTP 409：`Drain node jobs before rollback/finalize`。
初查有 64 个 pending、5 个 running；完成只读预检时为 60 个 pending、5 个 running，事后复核为 56 个 pending、5 个 running。
命令在进入目标/源全量校验及物理删除之前被拒绝。按任务书报错即保留现场，没有手工 rm，没有取消作业或重启服务，也没有接触 4090。

旧根 `/cloud/cloud-ssd1/Kiri4DGS/output/hololab` 是指向 SSD2 的兼容软链。真正待回收的是登记源备份：

```text
/cloud/cloud-ssd1/Kiri4DGS/output/.hololab-migration-source-fa13e786-b60c-4377-92ba-80a6101e9039
```

该备份仍为目录，设备/inode 为 `[64528, 31982534]`，与迁移身份一致；兼容软链保持不变。
迁移仍为 `cleanup_pending`，迁移维护 claim 为 0，没有把正在运行的作业冻结。

## SSD1 回收前后

| 指标 | 回收前 | 命令返回后 |
|---|---:|---:|
| 可用空间 | 33,126,309,888 B（30.851280 GiB） | 33,126,309,888 B（30.851280 GiB） |
| 源备份实际占用 | 446,300,041,216 B（415.649 GiB） | 保留 |
| 本次释放量 | — | **0 B** |

## 新盘验证

- 回收尝试前，3 个不同 job 的大文件，各 1,007,776,898 B（约 961 MiB），源备份、新盘、迁移清单的完整 SHA-256 三方一致。

| Job（前缀） | 文件 | 完整 SHA-256 |
|---|---|---|
| 03fb2084 | 4d_cams/images.bin | `cfc3fd5904a45f02dc74c017f9d153298f8dc15c6831b1fd3144e4672b6eace7` |
| 0d0e7b79 | model/sparse/0/images.bin | `cfc3fd5904a45f02dc74c017f9d153298f8dc15c6831b1fd3144e4672b6eace7` |
| 587cae81 | model/sparse/0/images.bin | `bc99181e3685573164c45ce5dcf597831256bdff83a6db5c106d2560a94a617f` |

- 拒绝回收后再次核对这 3 个文件大小及首尾 64 KiB，均与源备份一致、可读。
- 源备份共 92 个 `w/*/j/*` 目录全部存在于新盘，新盘共有 96 个。
- 其中任务书关注的 workflow `586915eb-0a2d-4566-b0f1-f6febd1957b3`：源备份 52 个 job 目录，新盘 56 个，原 52 个缺失数 0。
- 内部、外部软链各抽查 3 条，全部保持清单中的链接文本、可解析且可读。外部样本仍指向 SSD1 的 `Kiri4DGS/data/camarray/...`，未删除这些依赖。
- 6 个旧预览 URL 在回收尝试前后均返回 206，内容片段与迁移验收基线一致；缩略图返回 200 / image/jpeg。
- API health 为 200 / ok，主 workspace 根仍为 `/cloud/cloud-ssd2/hololab`。
- 本轮没有重新宣称做了全 workspace 全量校验；完整哈希抽检为上述 3 个大文件，服务验证为 6 个 URL 和缩略图。

## 两盘占用

统计时点：2026-11-06T11:03:00.052866+00:00 至 2026-11-06T11:03:11.826643+00:00。GiB = 1024³ B。运行中作业会使 SSD2 占用持续变化。

```text
Filesystem      Size  Used Avail Use% Mounted on
/dev/vdb        1.5T  1.4T   31G  98% /cloud/cloud-ssd1
/dev/vdc        984G  436G  498G  47% /cloud/cloud-ssd2
```

| 磁盘 | 总量 GiB | 已用 GiB | 可用 GiB |
|---|---:|---:|---:|
| /cloud/cloud-ssd1 | 1475.39 | 1379.51 | 30.85 |
| /cloud/cloud-ssd2 | 983.23 | 435.62 | 497.60 |

### Kiri4DGS 一级目录全景

`du -x -B1 --max-depth=1` 不跟随软链；占用为实际分配空间，不是文件逻辑大小。

| 目录 | 占用 GiB |
|---|---:|
| **总计** | 1079.158 |
| output | 620.034 |
| weights | 211.596 |
| SpacetimeGaussians | 94.686 |
| temp | 70.281 |
| data | 41.553 |
| tryouts | 12.504 |
| tmp | 10.886 |
| packages | 6.870 |
| hololab | 5.748 |
| .git | 4.519 |
| Utils | 0.342 |
| backups | 0.058 |
| results | 0.052 |
| pose-Benchmark | 0.011 |
| reports | 0.008 |
| sharp-4dgs | 0.005 |
| scripts | 0.002 |
| sparse-4dgs | 0.001 |
| .flops | 0.000 |
| .ruff_cache | 0.000 |
| env_helper | 0.000 |
| .pytest_cache | 0.000 |
| media | 0.000 |
| outputs_test | 0.000 |
| .flops_tmp | 0.000 |
| .vscode | 0.000 |
| .claude | 0.000 |

`output` 合计 620.034 GiB，其中源备份 415.649 GiB，其余约 204.385 GiB 均不在本次删除范围。

### SSD2 一级目录

| 目录 | 占用 GiB |
|---|---:|
| /cloud/cloud-ssd2 | 435.456 |
| /cloud/cloud-ssd2/hololab | 435.417 |
| /cloud/cloud-ssd2/.hololab-migration-fa13e786-b60c-4377-92ba-80a6101e9039 | 0.039 |
| /cloud/cloud-ssd2/lost+found | 0.000 |
| /cloud/cloud-ssd2/.hololab-migration-3bbc68eb-6d1c-4640-a035-2118ce3cde20 | 0.000 |

`weights`、`SpacetimeGaussians`、`data`、`temp`、`/cloud/cloud-ssd1/freetimegs-a800` 及 `Kiri4DGS/data/kiridog2` 均未删除。后两者已确认仍存在。
完整 output 子目录列表、精确字节数、SHA-256、预览及命令错误见同名 JSON。

## 建议和现场保留

1. 让当前批次正常结束，确认源节点 running、assigned、pending、orphaned 都已排空后，再使用官方回收入口；删除授权已经具备，无需重复确认。
2. 不手工删除兼容软链或源备份，不扩大到其他 output、data、weights 或 freetimegs-a800 目录。
3. 工具通过节点排空检查后，还会全量重读校验目标与源备份，再仅删除登记备份。现有 CLI HTTP 超时为 120 秒，大数据校验可能超时；下一次执行应使用足够长超时的官方 HTTP 客户端或改进后的后台任务接口，避免把客户端超时误认为服务端已停止。该项是代码只读核查发现，本轮没有修改工具或绕过检查。
4. 若最终成功回收 415.649 GiB 且其他占用不变，SSD1 可用空间预计约 446.501 GiB；这是估算，尚未释放。

本轮仅新增这份操作报告及 JSON 证据，没有修改实现代码。
