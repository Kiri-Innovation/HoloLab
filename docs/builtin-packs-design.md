# 随 HoloLab 分发的 builtin 算法包

状态：设计提案，2026-10-26。本次只写设计；不新增 scope 保护、不移动包、不改扫描、执行或前端代码。

## 结论

| 问题 | 建议 | 理由 |
|---|---|---|
| 命名 | 真包用 `builtin/get-index@0.1.0`；现有 `@builtin/*@1` 不变 | 保持普通包与引擎操作两套身份域，不扩充 intrinsic 白名单来承载脚本包 |
| 存放 | 独立 `hololab/builtin_packs/<name>@<version>/`，进入 Python wheel/sdist | 不依赖用户 `pack_dirs`，与可写安装目录分开；部署计算节点即可获得 |
| 版本 | 包独立 semver，本体发行清单固定包版本及代码哈希 | 本体升级不应导致所有包无意义升级；本体版本只是分发来源证据 |
| 身份 | 继续按普通算法包计算 content identity、写执行快照 | 随本体分发不等于免校验；不需要伪造注册表 publication |
| 迁移 | 先新增少量 builtin 真包，保留旧 `kiri/*` 和历史工作流 | 改 scope 是换包身份，不是无损别名；不得重写历史记录 |
| 批次 | 先来源保护/打包/执行/身份，再最小试点，最后扩展迁移 | 当前的运行环境和配方身份接口尚不足以只搬目录就交付 |

## 1. 命名与三种来源

- `builtin/*`：由本体发行物携带的真实算法包，manifest、独立 semver、脚本和代码身份齐全，工作流 `kind=algorithm`。界面称“随 HoloLab 提供”。
- `community/*`：官方维护但独立分发、需要安装的普通包，例如 `community/colmap-sfm`。这是官方保留命名空间的产品约定，不是自动产生可信性的字符串；中央授权规则需保证官方维护边界。
- 其他普通 scope：其它维护者或本地包。未带 scope 的历史名仍按既有规则补 `kiri/`。
- `@builtin/*`：继续仅表示引擎 intrinsic，固定协议版本 `1`，不参与真实包扫描。visualize 仍是视图特例，不生成对应算法包。

`builtin/get-index` 与 `@builtin/get-index` 字节不同，普通名称校验不接纳 `@`，canonicalization 只给无斜杠的旧名补 `kiri/`，不会剥离 `@`。前后端键都能区分；不能新增“忽略 @”的显示名匹配或模糊别名。新增 intrinsic 也必须经过现有专用校验，不能让 `builtin/*` 落入 intrinsic 分支。

用户看到的来源标签采用“随本体提供 / 官方社区包 / 其他包 / 引擎操作”，避免让用户仅凭一个 @ 理解差异。分类继续按 `manifest.category`，例如 `utility/arrayed`；scope 不是功能分类。拼错的 `buildin/*` 不自动转成 `builtin/*`。

依据：[前端身份比较](../hololab/frontend/src/packageIdentity.ts)、[后端身份入口](../hololab/package_identity.py)、[图节点类型与校验](../hololab/gateway/workflows.py)。

## 2. 存放、扫描与不可覆盖边界

建议结构（尚未创建）：

```
hololab/builtin_packs/
  index.json
  get-index@0.1.0/
    manifest.yaml       # name: builtin/get-index
    run.py
  array-length@0.1.0/
    manifest.yaml
    run.py
```

`index.json` 是发行清单，记录包名、独立版本、相对 manifest 路径、code_sha256、身份格式版本；本体 build/version 作为外层分发证据。路径从 Python 包资源定位，不相对 cwd、不硬编码 site-packages。wheel 和 sdist 都必须包含 manifest、脚本及清单；CI 从构建完成的 wheel 安装后重新验身份，防止仓库中有、安装后丢失。现有 [pyproject.toml](../pyproject.toml) 的 wheel 包范围是 `hololab`，但仍要验证资源是否实际入包，不能只凭目录推断。

**扫描分两种来源，而不是把内置目录塞到 pack_dirs 最前面：**

1. 每个计算节点自动读取本体资源清单；使用现有 manifest 加载器和内容身份逻辑。
2. 再扫描用户 `pack_dirs`。`origin=bundled/local/registry` 由可信加载入口生成，不接受 manifest 自称 bundled。网关按节点保留来源与本体发行版本，不能用网关自己的包清单代替远端节点的清单。
3. builtin scope 只接受发行清单中列出的资源。用户来源出现 `builtin/*` 时明确报“保留命名空间”，不按现有 first-wins 静默吞掉；即使用户目录先扫描、版本不同、目录名伪装或路径是符号链接，也不能抢占。
4. 普通来源维持现有扫描与冲突策略；开发者调试副本用自己的 scope。editable 安装允许本体开发，但清单哈希不匹配时不能继续冒充已验证发行包；明确显示开发状态或拒绝冻结执行。

现有 [LoadedPack/扫描器](../hololab/node/packs.py) 只有 source_dir 等信息，没有上述来源保护；这是待实现能力。

**安装与发布必须后端阻止：**

| 入口 | 设计行为 |
|---|---|
| `resolve/status builtin/*` | 只查节点的 bundled inventory；版本不在当前发行物时提示升级到包含该版本的本体，不访问 registry |
| `pack install builtin/*` 与网关 install API | 在查远端和创建目标目录之前拒绝；提示“由 HoloLab 提供，请通过本体升级更新” |
| `pack publish` | 校验规范名后拒绝 builtin scope，包括 file:// 源；复制到其它目录也不能绕过 |
| 安装目标目录 | 普通包也不能将目标选进 bundled resource 根或其子目录，须 resolve symlink 后检查；保护所有写入入口，不只检查 spec 的 scope |
| 中央 registry | 服务端也应保留 builtin scope、拒绝发布；客户端过滤不足以约束其它客户端。此项是注册表单独实施的依赖 |
| 社区目录与直接详情 | 不展示 builtin scope，不把本地 bundled inventory 混进社区列表；自定义源即使返回它也不能安装 |
| 本地 Pack Manager | 可查看、搜索、引用、检查环境和代码身份；显示“随 HoloLab 提供”，无安装/发布/卸载动作；保持按 category 的树 |

[当前网关安装落点](../hololab/gateway/pack_manager.py) 从 pack_dirs 选第一个可写集合；若把内置目录放入这组配置，存在误选风险。因此必须隔离 bundled inventory 与 writable destinations。文件只读权限是辅助措施，不是对同用户或管理员手工改文件的保证；手工改动由身份校验识别。

## 3. 版本与“无需安装”的实际含义

选择**包独立 semver + 本体发行清单锁定版本/哈希**，不用本体版本充当每个包的版本。不同 HoloLab 版本可携带相同包；同一包名和版本必须保持同一代码身份。修改脚本、接口或其它身份覆盖内容后必须升包版本，不能借“本体升级”覆盖它。

一份发行物可以携带兼容期内多个包版本，UI 默认选择清单指定的新版本，已有图仍精确引用原版本。建议至少保留上一兼容版本；删除旧版本需发行说明和兼容测试，具体保留窗口随体积/维护成本确定。跨计算节点调度按每台机器实际提供的精确版本及哈希匹配；缺版本时不能自动换成最近版本。

**当前迁移阻碍：** `get-index`、`array-length` 等 manifest 指定 `runtime.env: kiri`，而 [node/runtime.py](../hololab/node/runtime.py) 在执行时要求该 env 映射到 conda prefix；不是“没有 GPU 就随处能跑”。单纯换 scope/位置后，干净安装仍可能失败。

为真正实现开箱可用，首批仅收标准库和明确本体运行时支持的功能。设计新增显式 host 执行模式（最终字段名在 schema 实施时定），由 bundled 来源授权，使用节点自身 Python 的绝对路径启动子进程；不拼接 `python` PATH，不套 kiri conda，不把任意普通包升级为 host 特权。当前 shell 脚本依赖 Bash/GNU find/readlink 等，宜在试点中改为标准库实现并保持可见行为、数组枚举/符号链接/错误语义；平台支持范围如实声明。

环境仍独立记录，不能把“随本体提供”当作所有运行依赖均满足。需要 ffprobe、COLMAP、torch 等且未随本体保证的包应继续独立分发，不作为首批 builtin。

## 4. 身份、配方与快照

builtin 真包完整走普通算法包身份路径：

1. 构建发行清单时使用现有 `content_scope.capture()` 生成 code_sha256。以各包目录为 root，不能为省事把整个 HoloLab 源树设为 root，导致任意本体变化都改变所有包身份。
2. 节点加载时验证资源清单和内容；执行冻结时由目标节点重新计算并与清单/配方要求比对。保留执行前 controls 检查及执行后的验证。
3. workflow 引用保存 `builtin/get-index@x.y.z`；执行快照保留 `node.code_identity`、有效参数和环境证据。另记 origin=bundled、本体发行版本/构建标识，仅作 provenance，不替代包的内容哈希。
4. 不制造 registry source、发布者、yank 状态或安装收据。包未向中央发布也可产生有效代码身份。

现有链路已经支持没有 publication 的本地包执行时抓取身份：[节点 capture](../hololab/node/runtime.py)、[prepare 与快照](../hololab/gateway/preparation.py)。**但配方导出与检查目前从 `publication.code_sha256` 取哈希**（[recipe_files.py](../hololab/gateway/recipe_files.py)），不能直接沿用到 builtin 后误报“发布身份未记录”。需要一个统一的“已验证内容身份”读取接口：bundle 清单验证值、registry 收据值分别来自各自证据源；同名不同哈希严格报错，现有普通包规则不能被放宽。身份来源字段与 publication 解耦。

如果脚本导入包目录之外的 HoloLab helper，包自身哈希不能证明 helper 未变化。首批尽量自足；确需共享实现时，需将 helper 内容纳入受控依赖身份，或记录并严格验证单独的执行器/helper 身份。仅记本体版本号不足以覆盖 editable 修改。

快照保存证据不等于保存完整可执行源代码。历史重放必须提供原 bundle 版本/哈希（保留兼容资源或使用对应历史 HoloLab 发行环境），不能拿当前版本偷偷顶替；无需增设从社区下载 builtin 的后门。

## 5. 已核实的候选与迁移路径

本次只读取当前工作树。没有发现名为 `constant`、`constant-*` 或普通 `array-assemble` 的 manifest；不要把已存在的 intrinsic 再造一套同名脚本包。

| 当前真实包 | 建议 | 核实的限制 |
|---|---|---|
| `get-index@0.1.0/0.1.1` | 以 0.1.1 行为为首个 `builtin/get-index` 候选 | Bash/GNU 工具、kiri env；需标准库实现与旧行为回归 |
| `array-length@0.1.0/0.1.1` | 与 get-index 同批 | shell、kiri env，必须固定数组元素计数约定 |
| `arrayfy@0.1.0` | 第二批通用工具 | shell、链接语义、输入格式与零长度边界需测试 |
| `slice@0.1.0`、`regroup@0.2.0` | 第二批候选 | 有标准库 Python 脚本但仍使用 kiri env；轴语义要兼容 |
| `regroup-by-frame@0.1.0/0.1.1` | 后续评估 | frame 领域约定，不能仅因 category=utility 就认定通用 |
| `flatten-image-groups@0.1.0` | 暂保留独立包 | image/group 命名与隐藏映射侧车有专门契约 |
| `video-array-assemble@0.1.0` | 不进首批 | 真包，调用 ffprobe，不是 `@builtin/array-assemble` 的替身 |
| `colmap-assemble@0.1.0/0.2.0` | 保持独立领域包 | COLMAP 数据领域组装，不是通用数组 intrinsic |
| `flatten-array@0.1.0` | 待作者完成后评估 | 当前是未跟踪用户工作，设计不纳入迁移承诺、不改动 |

首批新增 `builtin/get-index@0.1.0` 等独立身份，0.1.0 是新命名空间的起点，不宣称等同旧包版本；若运行时适配改变算法语义，则按新包实际契约记录。原 `packs/get-index@0.1.1` 等继续保留，不改名、不删除、不把 `kiri/get-index` 自动 alias 到 builtin，也不更改 `DEFAULT_SCOPE`。

新建图优先推荐 builtin；旧草稿可后续提供显式迁移操作：展示名称、版本、输入输出/参数及代码身份变化，验证兼容后保存成新草稿修订。已执行快照保持原身份。迁移完并有兼容数据后，才讨论旧包弃用；不是本提案直接搬目录。

## 6. 分批与验收

| 批次 | 工作 | 完成条件 |
|---|---|---|
| A：基础边界（中） | 资源目录/发行清单、wheel/sdist 打包、来源字段、自动发现、所有安装/发布/覆盖保护、中央保留 scope 对齐 | 空 pack_dirs 仍发现内置包；外部同名包和 symlink 目标不能覆盖；不访问远端 builtin |
| B：最小可运行闭环（中偏大） | host 子进程模式、get-index/array-length 标准库适配、代码身份及配方导出解耦、精确版本匹配 | 干净安装、无 kiri conda、无网络可执行；篡改哈希拒绝；配方导出/导入/快照一致；不同版本节点不误调度 |
| C：产品呈现与渐进迁移（中） | 本地“随 HoloLab 提供”标签、社区隐藏、禁用不适用动作、新图推荐、显式旧草稿迁移 | intrinsic UI 不变；分类仍按 category；旧图和历史快照不被改写 |
| D：扩充与兼容维护（按包） | arrayfy/slice/regroup 等逐包评估、旧版本保留与弃用策略 | 每包边界及历史样例回归；无额外重依赖；资源体积可控 |

这里的工作量是相对范围判断，不是交付日期承诺。A+B 是首个可交付单元；只做重命名/搬文件不构成 builtin 功能。首轮不迁移 constants/intrinsic，不为 builtin 新建中央发布流程，不把所有轻量包都自动归入内置。

测试重点：裸 scope 与 @ 前缀不混淆；安装/发布 CLI 和 API 同时拒绝；普通包安装不误选 bundled 目录；wheel/sdist 干净安装；离线运行；host env 隔离；跨节点精确版本与哈希；脚本/共享 helper 改动检测；配方导出哈希；历史重放缺代码时明确失败；编辑器 PACKS 与 Pack Manager 分类行为不变。
