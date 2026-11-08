---
title: 包环境与依赖
group: Collaboration and reproducibility
order: 145
---
# 包环境与依赖

安装包让节点获得代码，准备环境才让代码具备执行条件。这是两个独立步骤；作者应声明依赖，让部署者能核对运行要求。

## 先安装代码，再准备环境
```bash
hololab pack install community/example@1.0.0
hololab pack setup community/example@1.0.0 --dry-run
hololab pack setup community/example@1.0.0
```

将示例身份替换为注册表中实际存在的精确版本，在执行节点上使用该节点的配置运行。`pack install` 下载并校验注册表归档，安装到 `node/installed_packs/`；它不创建算法环境，也不下载模型权重。仓库的 `community_packs/` 是源码归档，不是已安装包目录。

`pack setup` 读取已安装 manifest，创建或复用 conda 环境，安装声明的 conda 和 Python 依赖，再按需执行已校验的 `setup.script`。`--dry-run` 只预览动作，不创建环境、不运行脚本。系统应已能找到 conda。host runtime 包会跳过这条 conda 准备流程。执行凭据可以让未变化且已成功的脚本跳过重跑；脚本本身仍应支持安全重试。

## 在 manifest 中声明依赖

下面只展示相关字段，不是完整 manifest：
```yaml
runtime:
  env: image-tools
requires:
  python: ">=3.10,<3.13"
  conda_packages:
    - "conda-forge::ffmpeg=7.*"
  packages:
    - "Pillow==11.0.0"
setup:
  script: setup.sh
```

- `requires.python` 接受 PEP 440 版本约束或精确版本。选择受支持的解释器版本；环境创建不承诺复现精确补丁版本。
- `requires.conda_packages` 接受 `[channel::]name[=version[.*]]`，如上例；不支持 URL、build 字符串或任意命令选项。
- `requires.packages` 使用 Python **发行包名称**，不一定与 import 名相同，可带 PEP 440 版本约束。不接受 URL、extras 或条件 marker。setup 通常先尝试 conda-forge，再回退到 pip；声明 `package_indexes` 可以为指定包选择索引。
- 除 Python 包外，也要声明原生库和构建前置依赖。可选的 `cuda` 表示驱动至少支持的 CUDA 版本，不代表 toolkit 已安装；GPU 要求也不保证当前有足够空闲显存。

需要可复现时，优先声明实际验证过的精确依赖版本。计划共用环境的包应保持声明兼容。已发布 manifest 不可变，更改依赖应发布新包版本，参见[包身份](package-identity-registry.md)。

## 逻辑环境名与实际环境

`runtime.env` 是逻辑名，通过节点 `envs` 映射到实际环境。没有映射时，setup 创建或查找同名 conda 环境，再记录 prefix。命名环境位于 conda 配置的 `envs_dirs` 中，通常是第一个可写目录，不在已安装包内部。已有映射会保留；setup 不会悄悄重建环境或替换已有解释器。

算法环境与 HoloLab 应用自身的环境分离，但**并非每个包、每个版本都独占一个环境**。逻辑名相同的包共用环境，不同逻辑名也可能映射到同一实际 prefix。`hololab pack setup --all` 按逻辑名合并要求，并检查组内声明冲突；它不提供锁文件，也不保证发现不同逻辑名指向同一 prefix 时的全部冲突。需要隔离依赖时，应同时使用不同逻辑名和实际 prefix。

## 把权重下载到节点存储

模型权重不进 Git，也不进入发布包归档。将下载逻辑放进包内相对路径指定的 `setup.script`，资源下载到节点缓存，默认是 `$HOLOLAB_HOME/node/weights/`，不要修改已安装的包源码。应使用已知校验和验证下载，并遵守模型许可与访问要求。

setup 脚本与任务会收到相同的节点缓存变量：`TORCH_HOME`、`HF_HOME`、`HF_HUB_CACHE`。使用这些库缓存位置，让安装准备和正式运行复用下载。节点 `cache_root` 和各库的 `cache_dirs` 可以覆盖默认位置，不要写死某台机器的路径。脚本还会收到 `PACKAGE_ROOT`、`CONDA_PREFIX` 和 `PYTHON`，供访问源码和使用所选解释器；不要误用终端中无关的 Python。自定义格式的下载器与运行代码，应约定配置缓存下的固定位置。

目录归属和清理规则见[存储目录布局](../use/storage-layout.md)。产物迁移不搬运权重或环境 prefix。

## 检查是否就绪，理解检查边界

在画布 Pack Manager 查看环境是否就绪、是否缺少准备步骤，在工作流的本地能力检查中核对声明要求。安装依赖后重新检查。环境缺失、版本不符都应在选定的执行节点修复，而不是在浏览器所在机器修复。检查读取可获得的元数据和已声明能力，不会自动安装软件。

检查通过，只说明当时已声明、可探测的要求满足。它不能证明所有 import 都能成功、原生扩展 ABI 兼容、editable 源码匹配或完整任务必定成功。未声明要求的包仍是未声明状态，不会因此获得可复现保证。

**已知边界：依赖版本可能漂移。** 向同一实际环境安装另一个包，或在 setup 脚本中安装依赖，可能改变原先满足要求的版本，界面随后会提示版本不符。建议精确声明验证过的版本，安装后核对所有受影响的包；不兼容的依赖应分配独立 prefix。逐个安装包不等于冻结环境。环境检查证据只记录一个时点，不是环境锁，也不把实测运行环境纳入代码哈希。
