# 静态中央注册表 v1

中央权威是一个可写的 `file://` 目录；`index.json` 与 `objects/<archive_sha256>.tar.gz`
可整体镜像到 HTTP(S)。不需要动态服务。节点 `pack_sources` 按顺序列出同一权威的镜像，
配方仍只保存 name/version。当前发布仅支持本地目录，HTTP(S) 为只读源。

## 索引与发布

索引包含 format=1、authority、以 canonical name@version 为键的 releases。
每条记录包含 name/version、code_sha256、archive_sha256、archive/manifest 相对地址、
archive_bytes/unpacked_bytes、env/requires、external、完整 evidence、published_at/publisher、
yanked/yank_reason/yank_updated_at。哈希提供完整性；发布者字段不是签名。
源的可信度依赖本地目录权限或 HTTPS 源配置。

`pack publish` 使用已有 Scope.entries 枚举热/冷内容，沿用 `.packignore`，
选中的 manifest 与忽略文件仍必入身份。保持 root 与 manifest 的相对布局，
只在归档中将 dev 的末尾 `+` 去掉；源目录不改。发版刷新冷基线，复制后重算身份，
与源不一致则拒绝。包体记录符号链接本身，不跟随；外部依赖不会自动打进包。
因此成功发布不等于任意算法均可独立搬运或环境可复现。

写入者用 flock 串行化；先持久化内容对象，再原子替换索引。同版本重复发布一律拒绝，
不同身份给出冲突。对象不可覆盖；失败可能留下未引用对象，v1 没有自动回收。
索引最大 16 MiB，归档最多 200,000 条目。旧包无需迁移。

## 安装与三态

`pack install/resolve` 优先本地：已有包不重复下载，源离线仍可用。
有期望哈希或安装收据时比较完整身份；旧本地包没有这两者时明确标为
“未提供期望哈希，不声称中央认证”，不伪造中央匹配。

- 找到且哈希匹配：可安装；索引命中但未下载时明确“包体将在安装时校验”。
- 找不到：明确错误；有源不可达时报告源不可达，不伪称不存在。
- 找到但哈希不匹配：硬停，不覆盖、不回退另一源、不提供强制安装。

安装先检查传输 SHA-256 与字节数，再安全解包，重算热、冷、manifest 总身份。
拒绝路径穿越、硬链接、特殊文件与通过符号链接写文件。成功后原子提升目录。
落点为 `<destination>/<scope>/<name>@<version>/content/`，保留原相对布局；
外层 `.hololab-install.json` 保存来源、发布记录和安装时身份，位于哈希范围外。
扫描器识别收据。节点 `config.yaml` 可独立指定社区包安装目录：

```yaml
install_dir: /home/user/hololab-installed-packs
pack_dirs:
  - /home/user/my-algorithm-repo
```

`install_dir` 必须是绝对目录路径，首次安装时自动创建。它隐式加入扫描和文件服务，
并排在 `pack_dirs` 前：同名同版本优先使用安装目录中的包；内置包的保留命名空间规则不变。
无需把它重复写入 `pack_dirs`。`pack_dirs` 继续用于本地开发包来源。
修改配置文件后重启节点；之后页面安装完成会立即重扫注册，无需再重启。
安装弹窗只读显示节点报告的目标目录，不提供目录编辑框。

未配置 `install_dir` 时保持兼容：页面安装选择 `pack_dirs` 中第一个集合目录
（跳过单 manifest／单包，仓库有 `packs/` 时进入该目录）；CLI 默认用 `pack_dirs[0]`。
CLI `pack install --destination` 优先于 `install_dir`；指定其它目录时仍需将该目录加入
`pack_dirs` 才能长期扫描，安装命令不会自动修改配置。已有同版本且哈希匹配的本地包
仍直接复用，不因设置 `install_dir` 自动迁移或重新下载。环境依赖仍需单独准备。
运行沿用已有的 manifest/ignore 优先、热范围启动前校验；冷代码仍只记基线。
注册表不可变不等于安装目录只读，也不保证运行途中源码不变。

## yank 与历史

`yank` 拒绝新安装，不删除对象；已有安装与精确历史身份仍可解析。
`unyank` 可恢复，重复相同操作幂等，仅改变 yank 元数据，不改发布身份。
离线解析不要求查询中央；UI 安装收据状态明确标注“安装时记录”，可以手动查询当前状态。
旧节点不支持查询时说明需升级，不影响原有运行。

发布后按包名、dev 对应版本与代码哈希扫描快照。匹配结果写独立
`published_as_notes` 表，不改 snapshot.graph_json 或旧 code_evidence。
无身份的历史快照不补算、不关联。注记失败不撤销已成功发布，CLI 明示并提供
`pack annotate` 修复；可用 `--snapshot-db` 指定数据库。

## 本地自验

为避免影响现有节点，准备临时配置（envs 按样例包实际要求填写）：

```yaml
pack_dirs: [/tmp/registry-demo/installed]
pack_sources: [file:///tmp/registry-demo/central]
envs: {test: /your/conda/env}
```

```bash
hololab pack publish /path/to/self-contained-pack --source file:///tmp/registry-demo/central --publisher local --snapshot-db /tmp/registry-demo/snapshots.sqlite
hololab pack install kiri/example@0.0.1 --config /tmp/registry-demo/node.yaml
hololab pack resolve kiri/example@0.0.1 --config /tmp/registry-demo/node.yaml
hololab pack yank kiri/example@0.0.1 --source file:///tmp/registry-demo/central --reason regression
hololab pack status kiri/example@0.0.1 --config /tmp/registry-demo/node.yaml
hololab pack unyank kiri/example@0.0.1 --source file:///tmp/registry-demo/central
```

若临时数据库不存在，注记扫描报告 no_snapshot_database，不创建历史。
完整自动化真实进程验证：`python -m pytest tests/test_pack_registry.py -q -s`。
覆盖不可变拒绝、内容哈希篡改、dev 发布等价与历史原文不变、yank/offline 运行、
file:// 与本机 HTTP 安装，以及恶意归档路径拒绝。

未实现：私有作用域授权、动态注册服务、公网部署/跨机分发、签名体系、
自动环境安装与任意外部依赖封装。现有 91 个包不自动发布或迁移。

## 本机验收记录（2026-10-24）

- 完整后端：1557 passed、5 skipped（133.25 秒）。
- 注册表专项：8 passed；新增 UI：2 passed；`npm run build` 成功。
- CLI 发布 `kiri/registry-echo@0.0.1+`，file:// 安装到临时干净目录，
  NodeRuntime 启动真实子进程；yank 前后各执行一次，均输出 REGISTRY_E2E_OK。
  正式身份 `425e2b8709b15c8778ad613155b4f0f6bdfbfa1c98a3a1c5efde3336613da2dc`，
  tarball 551 字节；另验证本机 HTTP 镜像安装。
- 同版本同内容/不同内容分别拒绝；篡改索引 code_sha256 后安装硬停、无正式落点。
- yank 后新装拒绝，已有安装离线运行、历史精确哈希解析成功；unyank 幂等。
- `0.1.1+` 发布后代码身份相同；dev 快照加注一次，重复扫描不重复加注，
  数据库原 graph_json 字节不变。
- 实际配置扫描为 94 个唯一包（较需求背景数有增长）；本轮未移动或改写现有包，
  验证前后 94 个 manifest 的 SHA-256 无变化。

日志位于本机 `/tmp/hololab-registry-full-final.log`、`/tmp/hololab-registry-e2e-final.log`、
`/tmp/hololab-registry-ui.log`、`/tmp/hololab-registry-build.log`。未连接公网注册服务，
未重启现有服务；真实运行验证在临时工作区中直接调用节点执行流程。
