---
title: 安装和运行
group: 快速开始
order: 10
---
# 安装和运行

先在自己的机器上启动 HoloLab，再用浏览器打开界面。

## 准备环境

发行版的预构建 tarball 已带前端产物和解释器，不需要 Node.js，也不需要在系统里准备 Python；下面的要求只适用于从源码安装。

从源码安装至少需要 Python 3.10（项目的 `requires-python` 为 `>=3.10`）、Node.js 22 和 Git。算法的依赖由各自的运行环境提供；需要 conda 的 Pack，还要准备 conda、Miniconda 或兼容环境。安装 HoloLab 不会替你安装算法所需的 CUDA、模型权重或数据集。

在 Ubuntu 24.04 上，`python3 -m venv` 缺少对应的 venv 系统包会报 `ensurepip is not available`；而系统 `apt` 提供的 Node.js 18 也不满足要求。先取得 `sudo` 权限安装系统依赖（Python 版本不是 3.12 时，把包名中的 `3.12` 换成对应版本）：

```bash
sudo apt update
sudo apt install -y git curl python3.12-venv
```

然后用 nvm 安装 Node.js 22。安装脚本结束后重新打开终端；下面以 Bash 为例立即加载它，最终 `node --version` 应显示 22.x：

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install 22
node --version
```

## 从源码安装

### 获取代码（二选一）

**全新克隆。** 前端构建会自动构建随仓库固定版本的 ColmapUtil 子模块，因此克隆时需要 `--recurse-submodules`。在终端执行：

```bash
git clone --recurse-submodules https://github.com/Kiri-Innovation/HoloLab.git
cd HoloLab
```

**已有仓库（包括 Kiri4DGS）。** 无需再次克隆。如果你在 Kiri4DGS 仓库内工作，HoloLab 已作为子模块包含在 `hololab/` 中，从 Kiri4DGS 根目录执行 `cd hololab`；其他已有仓库用户进入自己的 HoloLab 根目录。然后在 HoloLab 根目录初始化并更新子模块：

```bash
git submodule update --init --recursive
```

### 安装与构建（通用）

完成上面任一选项后，在 HoloLab 根目录执行以下命令。虚拟环境路径适用于 Linux 和 macOS。

```bash
python3 -m venv .venv-runtime
.venv-runtime/bin/pip install -e ".[dev]"
cd hololab/frontend
npm ci
npm run build
cd ../..
```

## 准备 Pack 运行环境

构建完成后，Packs 出现在列表里并不代表算法环境已经存在。在 HoloLab 根目录执行：

```bash
.venv-runtime/bin/hololab pack setup --all
```

该命令按每个 Pack 声明的 `runtime.env` 创建或补全环境，把同一环境的依赖合并安装；缺失的映射会写回 `~/.hololab/node/config.yaml`，原配置会先备份。完成时会显示 `Setup complete. Restart hololab/node so the environment mapping and cache take effect.`；若 HoloLab 已在运行，重启它后新映射和环境缓存才会生效。

## 启动服务

```bash
.venv-runtime/bin/hololab start --host 127.0.0.1
```

这个命令同时启动网关和本机计算节点；终端保持运行，按 Ctrl+C 退出。

这里显式指定了本机地址。省略 `--host` 时默认监听 `0.0.0.0`，可从局域网访问。端口冲突时可以加 `--port 8830`。

另开终端可以查看状态：

```bash
.venv-runtime/bin/hololab status
```

## 打开 Web 画布

按上一节的命令启动后，在浏览器中打开 [http://127.0.0.1:8828](http://127.0.0.1:8828)。如果改过监听地址或端口，以启动终端里 `open` 后面的地址为准。有多个实例时，URL 中的主机和端口决定你连接的是哪一个。

打开后首先看到工作流列表。点击已有工作流即可进入，也可以点击 **+ New workflow**；列表为空时还有 **+ Create your first workflow** 入口。新建后会打开空画布，地址变为同一主机和端口下的 `/w/<workflow_id>`，可以收藏这个页面，下次直接回到该工作流。

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/gallery-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/gallery-light.webp" alt="工作流列表，仅保留 classic STG 示例；名称、更新时间和其他工作流已隐藏。" width="656" height="280" loading="lazy" decoding="async" /></picture>
<figcaption>工作流列表，仅保留 classic STG 示例；名称、更新时间和其他工作流已隐藏。</figcaption>
</figure>

在桌面布局中，先认识这几个入口：

- **① 画布区**：中央区域放置节点和连线，用来组织工作流的步骤与连接。
- **② Packs**：左侧面板列出可用算法，通过 **Search packs…** 搜索，再将对应节点添加到画布。
- **③ 运行与状态**：顶部的 **Run** 用来启动工作流；底部 dock 的 **Jobs** 标签页内是 **Recent jobs** 与任务日志入口；右侧 dock 的 **Runs** 标签页可以回看运行记录。
- **④ 产物**：展开节点预览可查看受支持的输出；要查找产物清单，回到工作流列表，打开 **Artifacts**（`/artifacts`）。

<figure class="doc-screenshot">
<div class="annotated-canvas"><picture><source srcset="/images/docs/canvas-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/canvas-light.webp" alt="编号对应上方列表：① 画布区，② Packs，③ 顶部 Run / 右栏 Runs / 底部 Jobs 标签，④ 节点预览。Artifacts 产物清单在另一页面。原始影像预览及标识已隐藏。" width="3200" height="2100" loading="lazy" decoding="async" /></picture>
<div class="canvas-annotations" aria-hidden="true"><span class="canvas-region region-canvas"><span class="region-number">1</span></span><span class="canvas-region region-packs"><span class="region-number">2</span></span><span class="canvas-region region-run"><span class="region-number">3</span></span><span class="canvas-region region-history"><span class="region-number">3</span></span><span class="canvas-region region-jobs"><span class="region-number">3</span></span><span class="canvas-region region-preview"><span class="region-number">4</span></span></div></div>
<figcaption>编号对应上方列表：① 画布区，② Packs，③ 顶部 Run / 右栏 Runs / 底部 Jobs 标签，④ 节点预览。Artifacts 产物清单在另一页面。原始影像预览及标识已隐藏。</figcaption>
</figure>

## 先确认设备和 Pack

进入工作流后，在底部 dock 的 **Compute Nodes · Pulse** 标签页确认计算设备在线；设备卡右上角的齿轮是设置入口。**Packs** 列表来自计算节点扫描到的 manifest；看得到 Pack 不代表它所需的算法环境已安装。

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/panels-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/panels-light.webp" alt="左侧 Packs 面板与底部 Compute Nodes · Pulse 标签页的局部截图并列：算法入口与设备状态；设备名称、ID 和路径已隐藏。" width="1536" height="724" loading="lazy" decoding="async" /></picture>
<figcaption>左侧 Packs 面板与底部 Compute Nodes · Pulse 标签页的局部截图并列：算法入口与设备状态；设备名称、ID 和路径已隐藏。</figcaption>
</figure>

算法是否需要 GPU、输入数据放在哪里，也要按所选 Pack 的要求准备。

熟悉这些入口并确认设备就绪后，跟着[第一个工作流](workflows.md)搭起步骤、连线并运行。如果打不开界面或设备不在线，先看[排错](troubleshooting.md)。
