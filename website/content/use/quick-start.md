---
title: 安装和运行
group: 快速开始
order: 10
---
# 安装和运行

先在自己的机器上启动 HoloLab，再用浏览器打开界面。

## 准备环境

源码安装需要 Python 3.10 或更高版本、Node.js 22 和 Git。算法的依赖由各自的运行环境提供；需要 conda 的 Pack，还要准备 conda、Miniconda 或兼容环境。安装 HoloLab 不会替你安装算法所需的 CUDA、模型权重或数据集。

## 从源码安装

在终端执行：

```bash
git clone --recurse-submodules https://github.com/Kiri-Innovation/HoloLab.git
cd HoloLab
python3 -m venv .venv-runtime
.venv-runtime/bin/pip install -e ".[dev]"
cd hololab/frontend
npm ci
npm run build
cd ../..
```

上面的虚拟环境路径适用于 Linux 和 macOS。前端构建会自动构建随仓库固定版本的 ColmapUtil 子模块，因此克隆时需要 `--recurse-submodules`。如果已有仓库，先运行 `git submodule update --init --recursive`。

## 启动并打开界面

```bash
.venv-runtime/bin/hololab start --host 127.0.0.1
```

打开 [http://127.0.0.1:8828](http://127.0.0.1:8828)。这个命令同时启动网关和本机计算节点；终端保持运行，按 Ctrl+C 退出。

这里显式指定了本机地址。省略 `--host` 时默认监听 `0.0.0.0`，可从局域网访问。端口冲突时可以加 `--port 8830`，然后打开相应地址。

另开终端可以查看状态：

```bash
.venv-runtime/bin/hololab status
```

## 先确认设备和 Pack

进入工作流后，在 **Compute nodes** 面板确认计算设备在线，在 **Packs** 面板查看可用算法。Pack 列表来自计算节点扫描到的 manifest；看得到 Pack 不代表它所需的算法环境已安装。

计算节点的配置默认在 `~/.hololab/node/config.yaml`。例如，一个 Pack 声明使用 `research` 环境时，需要在设备上配置对应的实际路径：

```yaml
conda_bin: /path/to/conda
envs:
  research: /path/to/conda/envs/research
```

把示例路径换成自己的路径。算法是否需要 GPU、输入数据放在哪里，也要按所选 Pack 的要求准备。

准备好后，继续[第一个工作流](workflows.md)。如果打不开界面或设备不在线，先看[排错](troubleshooting.md)。
