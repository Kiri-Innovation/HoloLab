# 声明范围身份

这是提交时记录、启动前校验的执行检查，不是不可变安装或沙箱。
校验之后、运行期间的修改（含懒加载）不在保证范围内。

```yaml
root: ..                    # 相对 manifest 所在目录，默认 .，不得越过最近 Git 仓根
cold: [thirdparty/]          # 相对 root 的目录/文件，记录发布基线，不逐次校验
source_entry: stg_state.py
exec:
  working_dir: .
  shell: python "{{ package_root }}/stg_state.py" ...
```

`manifest_dir` 和兼容绑定 `pack_dir` 指向 manifest 目录；`package_root` 指向解析后的根。
相对 working_dir、marker、预览/live glob、status_file 和 viewer entry 从根解析。
任意 shell 参数不自动改写，显式使用 package_root；运行输入/输出的绝对路径照常使用。
没有 root/cold 的老包继续使用自身目录；无历史身份的旧快照可运行，详情明确显示未记录。
没有身份能力的旧节点不能承接带身份的任务；自足老包仍可按旧协议执行并标记未记录。

根目录的 `.packignore` 使用 pathspec 的 gitignore 语义，不读取 `.gitignore`。
可自由排除代码，无警告或拦截；选中 manifest 和 `.packignore` 自身始终纳入控制身份。
固定排除 `.git`、`__pycache__`、`*.pyc`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`、`*.swp`、`.DS_Store`。
STG 使用 `/output/`、`/outputs/`、`/data/`、`/assets/`、`/packs/*`。忽略项只读元数据来统计，不读内容。
符号链接只记目标字符串，绝不跟随，因此目标代码本身不受覆盖。

每个文件按可执行位记 100644/100755，链接记 120000；条目为
`<mode> <relative-path>\0<64位小写 sha256>`，按路径的文件系统字节序排序后整体 SHA-256。
格式 2 的主摘要为 `canonical([2, hot.sha256, manifest.sha256, cold.sha256])`；
canonical 是按键排序、无空格、ensure_ascii=true 的 JSON UTF-8 的 SHA-256。
选中 manifest 从热树条目中移除，单独归一化为映射再计算身份，避免版本号和
`packs/name@version/manifest.yaml` 文件位置重新混入热摘要。`.packignore` 仍按原始字节进入热摘要。

manifest **仅排除顶层** `version`、`yank`、`yanked`、`yank_reason`、`yanked_at`、
`published_at`、`published_as`。当前 schema 中保留的完整字段清单是：
`apiVersion`、`kind`、`name`、`root`、`cold`、`external_code`、`description`、`category`、
`docs`、`source_entry`、`arrayable`、`inputs`、`outputs`、`params`、`runtime`、`requires`、`exec`、
`idempotency`、`node_viewers`、`hide_default_output_previews`、`previews`、`progress`。
所有未知字段与嵌套字段也保留，包括 params 下名为 version 的字段。
YAML 的注释、缩进、映射键顺序不影响语义身份；数组顺序与值影响身份。
description/docs 等非发布字段保守保留，未声称所有等价写法都有同一摘要。

绝对安装位置、记录时间、冷基线时间、Git commit/tree/dirty、统计值、实际运行参数
不额外参与代码身份。manifest 中声明的 runtime、参数定义/default、外部路径等仍参与；
迁移不会改写绝对路径字面值，便携性依赖相对路径。运行参数在快照中另行冻结。
`0.1.1+ → 0.1.1` 只改发布字段时语义身份不变，可以作为 published_as 追溯的依据；
本改动不创建发布关联索引，也不会改写历史版本标签。

**原始 manifest 字节摘要仍作为独立启动控制项保存和逐字节校验。**
所以发布/注释变动不改变代码身份，但会使使用原始 manifest 的旧运行重试失败，需要 Fork；
语义身份相同不能绕过控制文件校验。既有格式 1 身份沿用其原始算法校验，不转换、不回填。

提交时由目标节点读取并记录；冻结的执行图携带该记录，重试/续跑及 fan-out 复用。
节点先比较 manifest 字节，再比较 `.packignore` 字节，最后重算热摘要，均通过才启动进程。
不一致明确失败并给出来源/提交值/当前值；选择“按当前代码重新提交 / Fork”才能采用新代码。
手动刷新冷基线不会重写已经提交的记录。校验不读取冷代码内容。

## 快照记录（格式 2）

提交时冻结到 graph 节点的 `code_identity`，随后传给 job 的记录包括：

| 字段 | 含义 |
| --- | --- |
| format / sha256 | 算法格式号 / 声明范围总身份 |
| source / root / path / recorded_at | 提交时包名版本、声明根、解析路径、记录时间 |
| controls | 原始 manifest 与 .packignore 字节摘要，启动前先校验 |
| manifest | 归一化摘要、归一化映射、原始摘要、排除的发布字段清单 |
| hot | 热树摘要、文件/链接/字节统计、verified_at_submission |
| cold | 基线摘要、baseline_at、统计、not_checked、release、baseline_status、previous_sha256、content_changed |
| ignored / fixed_excluded | 忽略与固定排除的文件数、链接数、字节数、顶层目录 |
| git | commit、tree、dirty 等来源交叉记录 |
| uncovered / undeclared / dependency_discovery_complete | 声明的未覆盖来源、已知未声明来源、依赖发现是否完整 |
| quality_hints | 绝对路径等质量提示 |
| effective_params | 准备阶段已经解析的有效参数，不参与代码身份 |

没有冷范围时 baseline_at 为 null；不伪造检查时间。所有快照内字段都不可后续刷新。
手动/发版刷新只更新树外基线缓存，未来提交才能引用新基线；冷内容变化导致未来身份变化，
只更新时间而内容未变时身份不变。记录 baseline_status=updated 可显示“基线已更新”，
但 verification 仍为 not_checked，绝不把它展示为逐次验证结果。
重试/续跑使用原快照身份和参数；修改代码只能重新提交或 Fork，不覆盖旧摘要。
快照 API 的 code_evidence 从已保存执行图读取，不查询当前 catalog 补证据。
缺失身份显示“历史运行未记录代码身份”；不拿今天源码计算历史身份。

## 冷代码基线

运行详情里的“重新基线”按钮通过在线节点刷新，只供以后的提交使用。
发版使用 `hololab pack release /path/to/pack`，自动刷新当前版本冷基线；也可手动
`hololab pack rebaseline /path/to/pack`。节点首次遇到新版本时自动创建其基线。
命令使用节点默认 `code_baselines_dir`，自定义节点目录需传 `--baseline-dir`。
缓存是带锁的原子 JSON 写入，位于代码树之外；按根、包、版本、cold 声明、忽略规则分区。
每份只存摘要和统计（不复制代码）；不再使用的版本缓存可在没有提交操作时删除，
不影响旧运行中内嵌的证据，下一次提交会重新生成。
`release` 是本地发版证据动作，不上传、打包或注册包。

## 覆盖边界

Git 仅交叉记录；dirty 如实显示“含未提交改动”。没有 Git 的分发树以声明根为边界。
绝对路径保留为质量提示，不拦截。外部依赖用 `external_code` 声明，记录 covered=false；
STG 的 Utils/STG_Viewer、HoloViewerPy editable、simple_knn 原生 editable、conda rasterizer
均标为未覆盖。静态声明不是完整依赖发现，`dependency_discovery_complete=false`；
`undeclared` 只列已知但未声明的路径，不代表空列表就没有外部依赖。

根树是包的内容视图，多个 pack 可共用一棵树；移动整棵根树和其中的 manifest 不改变身份。
源文件目录之外的配置、数据、解释器、conda 和原生二进制不自动冻结或验证。
打包/导出后置；未来导出必须消费 `Scope.entries()` 的 hot/cold 条目，不能另写忽略规则。

## 本地验收（格式 1，2026-10-23，迁入 packs/ 之前的记录）

真实 `stg-init@0.1.1` / STG 根的记录：

| 范围 | 普通文件 | 链接 | 字节 |
| --- | ---: | ---: | ---: |
| 热 | 155 | 0 | 1,168,043 |
| 冷 | 6,959 | 1 | 247,151,422 |
| .packignore 排除 | 17,884 | 2,100 | 93,169,406,244 |

排除的顶层目录为 assets、data、output；忽略项没有读取文件内容。
热摘要 `aa9f0cbed09174cba829d4d24f8ad0e2470fb0a6876581b4a298f8774c396e14`；
主摘要 `3eade9cdb7f576a2a88a1e83f0e0c3e5946f5e3005326da23059a880ffdb3ea9`。
首次记录实测 3.6–6.9 秒，独立热校验约 30 ms；这些是本机观测，不是性能承诺。
Git dirty=true（原有 stg-train@0.3.0 参数编辑被保留，未收入迁移提交）。

从 hololab 仓执行：

```bash
python -m pytest tests/test_content_scope.py tests/test_content_identity_execution.py tests/test_stg_content_acceptance.py -q -s
```

真实树用例只把声明内容复制到 pytest 临时目录进行破坏性试验，不修改活动 STG 源码，
也不代表产品采用副本执行。它断言：移动后摘要相同、cwd/config 路径正确；
改副本 stg_state.py 失败；改冷代码仍通过且基线时间不变；改 assets 身份不变；
.packignore 仅多一个换行也先失败。节点用例还在 semaphore 排队期间改文件，
断言热代码/规则改变时执行器调用次数为零。

重启升级后的 gateway/node（由操作者执行），提交一次运行，打开运行日志详情展开
“声明范围身份”即可核对证据。历史记录没有身份时显示“历史运行 / 旧节点未记录”。

## STG packs/ 迁移（格式 1，2026-10-23）

11 个包现在位于 `SpacetimeGaussians/packs/`：
`stg-init@0.1.0`、`stg-init@0.1.1`、`stg-train@0.2.0`、`stg-train@0.3.0`、
`stg-train@0.4.0` 至 `stg-train@0.4.6`。manifest 均声明 `root: ../..`。
源文件仍在 STG 仓根；cwd `.`、相对 config、输出 marker/live_globs 不变；
5 个 viewer 的 entry 改成 `packs/<包>/viewers/training-diagnostics/index.html`。
`history_index` 仍相对 history_output，未改。

共享 `.packignore` 增加 `/packs/*`，无需每包的 `!` 例外。
统一枚举器强制保留选中 manifest，穿过被忽略的祖先目录读取它；兄弟包和所有 viewers
均排除在身份之外（包含本包 viewers），但静态服务仍可读取这些文件。
如果以后需要把 viewer 也纳入身份，应单独设计内容声明，不能往共享忽略规则里逐一
恢复所有包，否则会重新引入兄弟包耦合。

发现机制原先是直接目录加一层 scope 目录搜索，并非无限递归。
现在如果配置的仓根含 `packs/`，只扫描该容器；旧目录布局仍支持，排除 output/outputs/data/assets/.git。
目录名不是包身份，仍以 manifest 的 name/version 匹配。当前节点配置的是 STG 仓根，无需改配置。

本次实测（stg-init@0.1.1）：

| 项目 | 结果 |
| --- | --- |
| 迁移前热范围 | 155 文件 / 1,168,043 字节 / 一次扫描含哈希 26.38 ms |
| 迁移后热范围 | 130 文件 / 1,084,499 字节 / 7 次中位数 20.83 ms |
| 全树元数据枚举 | 25,225 文件 + 2,101 链接 / 93,420,722,701 字节 / 1.38 s |
| 节点发现 / Gateway catalog | 11 / 11 |
| Viewer 静态 HTTP 验证 | 15 个文件全部 200，字节与磁盘一致 |

实际包目录有 26 文件（11 manifest + 15 viewer），并非题述 29；保留选中 manifest，
排除其余 25 文件，因此热范围为 130。兄弟 manifest 修改不改变主身份，
本包 manifest 或 stg_state.py 修改会失败，均在临时副本断言。

最小真实执行：临时数据库记录、内存传输的 Gateway JobAssign 已派发成功；
读取迁移后的真实 manifest 并校验身份，再经真实 run_subprocess 执行
`/cloud/cloud-ssd1/envs/kiri/bin/python <root>/stg_state.py init --help`，退出码 0。
这是入口/派发冒烟，不是完整 GPU 训练；没有向生产 Gateway 提交训练任务，也未重启服务。

兼容性：旧工作流按 name/version 解析新位置；真实快照
`60f92f19-e950-4a54-8613-326cae43734a` 的 STG 节点没有历史 code_identity，仍走旧记录兼容路径。
已有 code_identity 的旧运行因 manifest/.packignore 字节改变会拒绝重试，需重新提交/Fork，
不覆盖旧摘要。旧绝对 manifest 路径、显式配置到旧包目录的 pack_dirs、缓存的旧 viewer URL
没有文件系统别名；刷新 catalog 或把显式配置改到新路径。历史证据里的原路径保留原样。

自验（hololab 仓）：

```bash
python -m pytest tests/test_pack_dirs.py tests/test_stg_pack_layout.py tests/test_stg_content_acceptance.py -q -s
```

服务由操作者重启加载发现逻辑更新；刷新界面后核对 STG 11 个包和新的 manifest 位置。

## 格式 2 验收（2026-10-23）

真实 STG 声明范围被复制到 `/tmp/pytest-of-root/.../moved` 后进行改动断言，
活动源码与生产数据没有被修改。新口径热树不重复计算选中 manifest：
129 普通文件 / 0 链接 / 1,081,937 字节，另有独立 manifest 2,562 字节，
与原先 130 文件 / 1,084,499 字节的覆盖一致。冷树 6,959 文件 + 1 链接 /
247,151,422 字节。忽略 17,909 文件 + 2,100 链接 / 93,169,489,860 字节，
顶层 assets、data、output、packs；首次完整记录本机实测 3.59 秒（含冷基线与忽略统计）。

声明范围身份：`5467b47443ab8b017b1b9e804d546edeff2c0b1ba07fb90d2187e38455e3c47d`。
热摘要：`77aeb7198ff9b024615fe25188c30b1560b5a4930d771adaec2b154a327d739f`。
冷摘要：`b5818abb298f54fd3d256d6e2c99a9b3c968cc8014fa3858faf10c7d4416562d`。
移动目录、只改 version、dev 转正式版并重算冷基线、加 yank 标记均保持声明范围身份。
修改 stg_state.py 导致身份改变和启动校验失败；冷内容修改不触发启动失败，
重新基线后新提交身份改变并标 baseline_status=updated，历史基线不变。
被排除 assets 修改身份不变；.packignore 增加换行使身份变化，旧任务在控制检查即失败。

生产 SQLite 只读核对：开始时 146 快照，其中 141 没有身份记录（题述 102 是更早数量）。
结束核对时原 146 条 graph_json 的逐条 SHA-256 全部不变；期间线上另新增 1 条，
共 147 条，仍有 141 条未记录。无回填、无生产数据库迁移。
历史详情由新的展示代码显示“历史运行未记录代码身份”，需要操作者重启服务加载。

```bash
python -m pytest tests/test_semantic_code_identity.py tests/test_code_identity_records.py tests/test_stg_content_acceptance.py -q -s
```

重启 gateway 与 node 后，新提交会记录格式 2；打开运行详情或快照横幅的
“声明范围身份”，查看热摘要、归一化 manifest、冷基线时间与有效参数。
旧格式 1 仍按原算法校验；旧快照没有证据时不补算。

本轮全量后端测试 1,531 passed / 5 skipped；补充快照 API 用例后，身份/快照/STG 定向
回归 16 passed。前端身份展示测试 2 passed，`npm run build` 成功。
全部真实树变更验收均使用临时副本，未重启服务、未 push。
