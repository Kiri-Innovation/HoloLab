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
STG 使用 `/output/`、`/outputs/`、`/data/`、`/assets/`。忽略项只读元数据来统计，不读内容。
符号链接只记目标字符串，绝不跟随，因此目标代码本身不受覆盖。

每个文件按可执行位记 100644/100755，链接记 120000；条目为
`<mode> <relative-path>\0<64位小写 sha256>`，按路径的文件系统字节序排序后整体 SHA-256。
主摘要组合格式号、manifest/.packignore 字节摘要、热摘要、冷基线摘要。
绝对位置、统计时间、Git commit/tree/dirty、环境与未覆盖声明的解释信息不额外参与身份；
但 manifest 的所有字节参与身份，因此修改其中的版本或任何声明也会改变身份。

提交时由目标节点读取并记录；冻结的执行图携带该记录，重试/续跑及 fan-out 复用。
节点先比较 manifest 字节，再比较 `.packignore` 字节，最后重算热摘要，均通过才启动进程。
不一致明确失败并给出来源/提交值/当前值；选择“按当前代码重新提交 / Fork”才能采用新代码。
手动刷新冷基线不会重写已经提交的记录。校验不读取冷代码内容。

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
