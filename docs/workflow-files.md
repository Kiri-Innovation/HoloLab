# Workflow 文件化设计（评审稿）

状态：评审中（未实施）。本文定义「workflow 草稿从数据库记录改为本地文件」的完整设计。

## 1. 背景与目标

现状：工作流草稿存在 `workflows.draft_json`（数据库），不可见、不可 diff、不可 git 管理跨机同步依赖 API。

目标：草稿的唯一权威形态是工作区里的本地 `.workflow` 文件；画布是该文件类型的编辑器（类比 VSCode 打开本地 md / Unity 打开资源文件）。执行证据（snapshots / jobs / handles）仍留在数据库。

原则：**源码（草稿文件）与执行证据（DB）分离**。文件是分发单位；DB 是运行历史。

## 2. 文件格式规格

### 2.1 基本

- 扩展名：`.workflow`
- 内容：UTF-8 JSON（pretty-print，2 空格缩进）
- 原子写入：先写 `<path>.tmp-<pid>`，成功后 `rename` 覆盖，防止自动保存中断损坏文件

### 2.2 顶层字段

```json
{
  "formatVersion": 1,
  "id": "b71fdcaa-ce43-4aa6-9723-a5f9721a9f56",
  "name": "classic STG (arrayed)",
  "graph": { ...（现 draft_json 的 graph 部分，含 nodes/edges/port_types）... }
}
```

- `id`：稳定 UUID，迁移自现 `workflow_id`，永不变。跨机、跨库的谱系关联都靠它。
- `name`：显示名。**文件名与显示名解耦**（改名互不牵连）。
- `graph`：与现 draft_json 的 graph 结构一致（schemaVersion 1），本设计不改图结构。

### 2.3 序列化稳定性（为 git 协作服务）

- 节点数组按 `id` 字典序排序后输出；键序固定（按 schema 定义顺序，不做字母序）。
- 目标：改一个参数 → git diff 只出现对应行。

### 2.4 formatVersion 规则

- 顶层第一字段 `formatVersion`，整数递增，**不用语义版本**。当前 `1`。
- 写入：永远写客户端支持的最高版。
- 读取：接受所有 ≤ 当前版；旧版经逐级迁移函数链（v1→v2→…）升级后使用。
- 遇到 > 当前版：**明确拒绝打开**，提示「文件由更新版本的客户端生成」，绝不静默降级。
- 命名避开 `schemaVersion`（端口声明/快照图已占用该名）。
- 迁移函数刻意保持轻量（映射级）；批量升级走 CLI `hololab workflow migrate`，
  **不随打开自动重写整库文件**（避免升级一次导致 git 全量 diff 噪音）。

## 3. 身份与路径模型（Unity 语义）

系统维护 **id ↔ path 双向注册**（存 DB 表 `workflow_files`，见 §6）：

| 情形 | 处理 |
|---|---|
| id 对、文件在 | 正常打开/编辑 |
| id 对、文件丢失 | 弹窗：**重新定位到磁盘上的新路径** / **在原路径新建空 workflow** |
| 路径在、文件内 id 不匹配 | 按 Unity 语义视为**不同资源**：询问当作新文件打开，还是重新关联 |
| 同 id 出现在两个文件 | 无效状态：警告并要求处理（通常是复制文件忘了改 id） |
| 文件名改了 | 只是 path 变化，更新注册表即可，id 与显示名不动 |

`workflow_id`（现 DB 主键）**退役前保持只读兼容**：所有谱系（snapshots/jobs/handles）继续用 id 关联，不断链。

## 4. 存储与配置

- 节点配置新增 `workflows_dir`（语义同 `pack_dirs`，可配置、watcher 监听）。
- 本机默认值：`/cloud/cloud-ssd1/Kiri4DGS`（迁移目标；该目录是 git 仓库，workflow 文件因此天然获得版本管理与跨机 push/pull 同步）。
- 文件进入 git 后按普通代码文件对待（提交与否由人决定；建议提交，理由见 §9）。

## 5. 网关行为与 API 变更

### 5.1 扫描与监听

- 启动时扫描 `workflows_dir` 递归 `*.workflow`，解析出 id/name，构建注册表。
- watcher 增量监听（复用 pack watcher 基建，含手动重扫兜底 `POST /api/workflows/reload`）。
- 扫描失败/解析失败的文件：跳过并记录 warning（不致命，同 pack 语义）。

### 5.2 API 变更清单

| API | 变化 |
|---|---|
| `GET /api/workflows` | 数据源从 DB 改为注册表；每项含 `id, name, path, missing(bool)` |
| `GET /api/workflows/{id}` | 读文件（经 formatVersion 迁移链） |
| `PUT /api/workflows/{id}/content` | **新增**：全量写文件（原子写；带 expected_mtime + base_rev）。**日常编辑不走它** —— 走 WS 结构化 patch（见 §13），它只服务导入/迁移/程序化写入 |
| `POST /api/workflows` | **本期不动**：保留旧语义（`{name, graph}` 存 DB 草稿），兼容现有前端；Phase 3 再评估废弃 |
| `POST /api/workflow-files` | **新增**：`{parent_dir, name}` → 写新文件，返回 id+path（Phase 2 前端使用） |
| `POST /api/workflows/{id}/relink` | **新增**：id ↔ path 重新关联（丢失后的重新定位） |
| `POST /api/workflows/reload` | **新增**：手动重扫（同 packs/reload 语义） |
| `DELETE /api/workflows/{id}` | 语义变化：删除 = 删文件（需确认）；仅解除关联不支持 |
| `/dispatch`、`/snapshots` | **读源解析顺序**：文件存在 → 读文件；文件缺失但 DB 草稿存在 → **用 DB 草稿（兼容期兜底）**；拿到内容后拷入 `snapshot.graph_json`。**迁移完成（所有工作流都有文件）后移除 DB 兜底**（Phase 3） |

兼容期：`GET` 同时带 DB 缓存兜底（文件被外部移走时仍能展示最近已知状态）。

## 6. DB 影响

- `workflows` 表**降级为派生缓存**（last-known name/path），派发路径不再读它；Phase 3 评估退役。
- 新增 `workflow_files(id PRIMARY KEY, path, mtime_seen)`：id↔path 注册表（网关内部索引，也可用 JSON 文件实现，倾向 DB 表——多节点共享同一网关时一致）。
- `snapshots / snapshot_jobs / jobs / handles`：**零改动**。谱系按 id 关联，不受本设计影响（参考 run/job 归属研究结论：bridge 表是权威，`jobs.snapshot_id` 仅出生地）。

### 6.1 与既有 recipe.json FileAuthority 的关系（重要）

现网已存在一套配方文件权威机制（`workflows.py:363 FileAuthority` + `workflow_recipe_files` / `workflow_recipe_intents` 表；GET 会自动生成 `recipe.json`，旧 POST 也写它）。**两者是不同的文档，本期互不合并**：

| | `.workflow`（本期新增） | `recipe.json`（既有） |
|---|---|---|
| 语义 | 草稿权威（画布编辑的东西） | 分发契约（在别处重建需要什么包/参数） |
| 谁写 | 网关（经 WS patch / PUT content） | 既有 FileAuthority 流程，本期不动 |

Phase 1 规则：
1. `FileAuthority` / recipe 相关代码**一行不改**；旧 POST 的全部行为（含写 recipe.json）原样保留。
2. **读取路径禁止隐式迁移**：扫描 `.workflow` 只认磁盘上已存在的文件；DB-only 的工作流**不会**因为在 GET 里被读到就自动生成 `.workflow` 文件（写文件只发生在显式迁移 CLI 与显式新建）。
3. `.workflow` 是否最终取代 recipe.json 的分发角色 —— 留作 Phase 3+ 的开放设计问题，本期不回答。

## 7. 前端交互

- 画布绑定文件路径；自动保存沿用现有时机（防抖落盘），UI 有「已保存/保存中」轻提示（不自造色值、不撑高节点）。
- **新建**：路径选择器（复用 PathInput）选目录 + 输入名字。
- **打开**：路径选择器选 `.workflow` 文件；最近打开列表（id↔path 注册表驱动）。
- **外部改动**：watcher 通知 → 若画布无未保存编辑，自动重载并轻提示；若有编辑，提示二选一（重载放弃本地 / 另存副本）。
- **记忆路径丢失**：§3 表格的弹窗流程。
- 节点状态角标 / Jobs 面板 / 右键刷新可视化：全部不受影响（按 id 关联）。

## 8. 迁移方案

一次性，命令 `hololab workflow migrate-files`：

1. 遍历 DB `workflows`，每个写 `<workflows_dir>/<name>.workflow`（重名自动加 `-2` 后缀并在报告里列出）。
2. 写入 `workflow_files` 注册表。
3. 报告：成功数、重名改名数、空草稿数。
4. 三台各自执行（A800 的草稿含他人未提交开发，迁移前与其负责人确认时机）。

## 9. 多机与 git 说明

4090 / A800 的 `/cloud/cloud-ssd1/Kiri4DGS` 是各自独立的盘（同名挂载点、不互通），同步走 git push→pull（既定约定）。workflow 文件放进该仓库即获得：

- 跨机同步：commit + push/pull，与代码同一条通道；
- 历史：文件级 diff/review；
- **代价**：`git status` 会常驻 workflow 文件改动（自动保存频繁落盘）。
  缓解：防抖写盘（现自动保存已有）；`.gitignore` 与否由使用者决定（不进 git 则失去同步与历史，进 git 则建议**编辑会话结束时提交**而非每次自动保存都提交）。
- **迁移后若仍用旧前端编辑**：旧前端写的是 DB 草稿，dispatch 读的是文件 → **编辑会被静默忽略**。
  缓解：Phase 2 前端与迁移同发布（切换后不再有旧前端入口）；网关检测到「DB 草稿与文件不一致」时记 warning 日志，便于发现漏网场景。

## 10. 边界情况清单

| 情形 | 处理 |
|---|---|
| 保存到只读路径/无权限 | 保存失败明确报错（含路径），不静默 |
| 进程在写入中途崩溃 | tmp+rename 保证文件不损坏 |
| git 合并冲突（JSON 冲突） | 稳定序列化下冲突可读；手解后 watcher 重载 |
| 同名文件出现在 workflows_dir 两个位置 | 各自独立按 id 区分；注册表显示两条 |
| `.workflow` 外的文件混入目录 | 忽略 |
| 文件被删但画布正开着 | 内存态仍在；保存时提示文件已消失，走 §3 流程 |
| formatVersion 升级 | §2.4 规则；批量走 CLI |

## 11. 分期实施

- **Phase 1（后端）**：文件模型 + 注册表 + 扫描/watcher + dispatch 读文件 + 迁移 CLI。
  兼容期：读路径「文件优先、DB 兜底」。
- **Phase 2（前端）**：编辑器绑定文件、新建/打开走路径选择器、丢失/外部改动处理。
- **Phase 3**：退役 DB 草稿读路径与 `workflows.draft_json`（保留表作缓存或删除，另定）。

## 12. 本设计不改的东西（明确划界）

- 图结构本身（nodes/edges/port_types schemaVersion 1）；
- 派发、快照、谱系、产物归属（我们已研究确认的 bridge 模型）；
- pack 体系、环境、文件服务。

## 13. 并发与一致性（多前端编辑同一 workflow）

### 13.1 问题

现状（DB 草稿）就是**无锁的后写覆盖前写**：两个前端先后 PUT `draft_json`，先写的一方改动被静默吞掉。文件化后若继续「前端各自写文件」，还会叠加磁盘竞态。所以一致性模型必须先定义，再动手。

### 13.2 模型：网关是唯一权威写入者（VS Code Remote 语义）

VS Code Remote 的做法：**文件只在服务端机器上，编辑器是瘦视图** —— 客户端把编辑流式发给服务端，服务端持有权威文档模型、负责落盘，再把变更广播给所有会话；客户端本地乐观显示。**磁盘写入永远只有服务端一个进程在做的。**

对应到 HoloLab：

```
画布前端（N 个）──WS patch──▶ 网关（权威文档模型 + rev 计数）
                                   │
                                   ├─ 广播 workflow_changed 给所有前端（含发起者 ack）
                                   └─ 防抖 + 原子写（tmp+rename）落盘
磁盘外部改动（git pull / 手改）──watcher──▶ 网关重载文档 → rev++ → 广播全量
```

**原则：前端永远不直接写文件；写路径收敛到文件所在机器的网关一个进程。**

### 13.3 协议

- **结构化 patch，不传全量**：画布编辑天然是结构化的（节点参数改了、节点挪了、加了条边）。
  WS 消息 `workflow_edit {id, base_rev, ops[]}`，ops 是元素级操作（`node.params.set` / `node.pos.set` / `edge.add` …）。
- **rev 单调递增**：每个 workflow 文档一个修订号。前端记录自己最后见过的 rev。
- **乐观本地应用**：本地先改画布（手感即时），同时发 patch；网关 ack 带新 rev。
- **冲突（base_rev 落后）**：网关拒绝并返回当前 rev 与状态。前端默认接受「元素级 last-write-wins」
  （不同节点同时改是常态、无冲突；同节点同字段同时改 → 后到者赢，可预期、可接受），
  并提供「重载放弃本地」逃生门。
- **外部改动 = 一次全量 rev**：watcher 发现文件变了 → 网关重载 → 广播全量文档 → 所有画布（含没在编辑的）自动刷新。
  这就是你要的「一边的改动立刻触发另一边的画布加载新状态」。

### 13.4 落盘

网关持有文档后，落盘仍是防抖 + tmp+rename（自动保存时序不变）；
因为写路径唯一，磁盘竞态从根上消失。

### 13.5 边界（说实话）

- **实时共编辑只成立于「连着同一个网关」的前端之间**。
  跨机器是另一回事：两台机各自持有文件副本，一致性走 git（commit + push/pull），不实时。
  这是文件形态的固有属性，和 VS Code（文件在远端机器、客户端都连那台）语义一致。
- **不做完整 OT/CRDT**：图编辑是低频结构化操作，rev + 元素级 LWW + 手动重载逃生门已经覆盖现实场景；
  全量 OT 的复杂度对当前收益不成立。
- **同一前端开两个标签页编辑同一 workflow**：等价于两个前端，走同一套 rev 流程。
