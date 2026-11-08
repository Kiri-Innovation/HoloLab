# Workflow 文件编辑上线验收（4090，2026-11-08）

前端改为即时 WS 元素 patch，网关 800ms 防抖原子落盘。新建、打开、丢失恢复、
不同 ID 询问、外部变更自动加载、并发 LWW 与重载逃生门均已接通。
4090 的 5 个 workflow 已迁移，已替换另一会话原先启动的实例；重启前未发现
train.py / stg_state / birefnet 训练进程。A800 / XENO-PC 未操作。

## 协议和动作覆盖

`workflow_edit {id, base_rev, request_id?, ops[]}` → `workflow_changed`。
接受 patch：rev + 1，全客户端广播（包括发起者）；落后 rev：只向发起者拒绝并返回
当前完整状态，前端重放本地字段，元素级 LWW。进程内 rev 单调；连接重建后重新读取
文档和 rev。接受与落盘分开确认，`saved:true` 才显示已保存/放行 Run。

日常 11 类 ops：`name.set`、`node.add/remove/set/pos.set/map.set`、
`edge.add/remove/set`、`graph.set/map.set`。兼容整个映射替换的
`node.params.set`、`node.port-values.set`、`node.bindings.set` 也保留。
完整字段定义见 [规格 §14](../workflow-files.md#14-实现协议与部署约定)。

核对 24 类动作：palette 拖入、菜单新增、复制、节点删除（单选/多选/键盘）、连线、
替换输入连接、断线/删边、节点移动、参数编辑（Inspector/固定项/数组）、内联端口值、
变量绑定、local_params、设备分配、arrayed、parallelism、组合槽/类型锚点、常量值、
视图标题、预览展开、workflow 改名、变量声明/改名/删除/值、参数固定项、算法替换、
快照克隆回草稿。所有持久化变更统一经过 canonical graph diff，不依赖各入口单独保存。

## 实迁和摘要

命令：`hololab workflow migrate-files --workflows-dir /cloud/cloud-ssd1/Kiri4DGS`。
结果：成功 5、重名后缀 1、空草稿 0、跳过 0。表中摘要为 SHA-256 前 12 位；
5/5 的完整摘要、id、GET 文件路径都一致。

| 文件（均在 /cloud/cloud-ssd1/Kiri4DGS） | id | 草稿投影摘要 | 含绑定执行图摘要 |
|---|---|---|---|
| large cam array.workflow | 8a19a507-e3d0-4085-8e0e-7dc22ac91a49 | d5b03344afc2 | db6dab2c42d4 |
| large cam array-2.workflow | 8deff95a-1bf6-4c55-bbb2-6cb6596e24fe | 69e970820cd6 | 358b0ce36634 |
| classic STG (arrayed).workflow | b71fdcaa-ce43-4aa6-9723-a5f9721a9f56 | b1ee2fe6116c | 1c5c9ebcaeb9 |
| orbit-pack-e2e.workflow | ca5d26f1-b30a-4c03-9511-31e4cb151d99 | 2455e60c2a0b | f2f0e1ef06b0 |
| 7CamArray.workflow | f1a8af3c-56d5-460f-849a-3cf68f06701c | cfee8550fa8c | f487c746908a |

发现的既有机制：draft_json 已把设备、local_params、变量值分离到 workflow_bindings；
既有迁移 CLI 的 `_get_cached` 会叠加这些值。因此磁盘 JSON 不与 raw draft_json
逐字节相同。本次保留该行为，分别验证去绑定的草稿投影与含绑定的完整执行图，两组
SHA-256 均一致；没有修改 FileAuthority、recipe.json 或谱系模型。

迁移前原始摘要 `/tmp/hololab-workflow-before.json` 与 DB 备份
`/tmp/hololab-workflow-before.sqlite` 已保留；含绑定基线从该迁移前备份计算，
不是从迁移后 DB 补采。最终核对结果 `/tmp/hololab-workflow-after.json`。

## 测试和复跑

新增 20 个测试：后端 13、前端单测 3、Playwright 4；另更新已有并发/参数固定项用例。
后端全量：2494 passed、44 个既有失败、7 skipped、16 subtests；所有失败均在
HEAD 基线复现集合内（额外配置本机 colmap4d 的 PYTHONPATH，消除原来的导入错误）。
最终文件/WS/恢复定向测试 34 passed；npm test 778 passed；build 成功。
相关 E2E 15 passed；另抽查的 6 个旧画布用例中 5 个失败，在独立 HEAD 工作树同样复现。

真实浏览器 + 两个 WS 客户端 + 文件编辑/删除/恢复 + 最便宜 source 节点派发：
`node hololab/frontend/e2e/workflow-files-live.mjs`（从 frontend 目录运行时去掉前缀）。
脚本在 finally 中通过 CAS 重置测试缓存，再逐字节恢复真实文件，不遗留探测 workflow。
实际 job/snapshot 作为执行证据保留；最终结果见同机 `/tmp/hololab-workflow-live-final.log`。

最终实测：参数编辑同步到另一真实浏览器，双 WS 客户端收到同一 rev；手改文件两边
自动加载，删除显示 relink/新建两项，恢复后对话框自动消失。`classic STG (arrayed)`
的 `videos` source 节点完成 job `64184012-5570-4f4b-a93a-18ba04b9381b`，
snapshot `602969f2-5e8c-47ba-8348-aaebb64614e4` 含仅磁盘存在的位置标记，证明从文件派发。
10 次 WS patch RTT：最小 8.84ms、中位 11.20ms、P95/最大 3367.72ms。
`/health` 与 `/api/health` 均 200，workflow_files 登记 5 条，所有 path 非空。

其它两台各自在协调好编辑时机后执行：

```bash
python tools/workflow_file_audit.py > /tmp/workflows-before.json
hololab workflow migrate-files --workflows-dir <本机目标目录>
python tools/workflow_file_audit.py --before /tmp/workflows-before.json --gateway http://127.0.0.1:8828
```

部署新版前端后刷新旧标签页；DB 兜底继续保留给尚未迁移的机器。
