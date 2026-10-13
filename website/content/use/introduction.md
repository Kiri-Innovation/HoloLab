---
title: 介绍 HoloLab
group: 开始使用
order: 0
---
# 介绍 HoloLab

做视觉实验时，你可能要把抽帧、重建、训练几个步骤串起来，反复改参数、看结果。HoloLab 像一张可以派工的实验流程图，让你在画布上安排这些步骤。

## 从管线变为画布上的工作流

我们常把多个算法步骤写进一个 Python 或 Shell 脚本，按顺序执行；调整管线时，就去修改这个脚本。

在 HoloLab 中，步骤之间的组织关系改由画布上的一张图表达，称为**工作流**（Workflow）。算法本身仍由原来的程序执行，你可以通过编辑这张图调整它们的连接方式。

## 画布上的方块和线

假设你已经有一个视频，平时会先调用抽帧脚本，再把输出交给下一步。

在画布上，执行抽帧的方块叫**节点**（Node），接收和交出数据的接口叫**端口**，接口之间用**连线**（Link）连接。选中节点可以改参数，运行后也能在这里看状态。

<figure class="concept-diagram" aria-label="把视频交给抽帧步骤，得到一组图片：连线连接上游输出端口和下游输入端口">
<div class="concept-flow">
<div class="concept-card"><strong>视频来源</strong><span>输出端口：视频</span></div>
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h17m-6-6 6 6-6 6" /></svg>
<div class="concept-card"><strong>抽帧</strong><span>输入端口：视频</span><span>输出端口：图片集合</span></div>
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h17m-6-6 6 6-6 6" /></svg>
<div class="concept-card"><strong>得到一组图片</strong><span>每个元素是一张图片</span></div>
</div>
<figcaption>原来要把抽帧脚本的输出路径交给下一步；在画布上，用连线表达这次数据交接。</figcaption>
</figure>

## 已有的算法怎么放进来？

你手头的算法可能是一个 Python 程序，也可能靠 Shell 命令启动。

用 manifest 说明输入、输出、参数、环境和命令，就接成了**算法包**（Pack）。设备扫描后，画布便能添加对应节点；环境和数据仍需先准备。

## 不必等跑到一半才发现接错

传错输出目录时，你可能要等下一步报错，才发现它需要的是图片而不是视频。

在 HoloLab 中，端口用机器可读的声明描述数据，拖线时就做**类型检查**，接错会被拒绝。数组层数和已命名轴也会检查（轴名默认须一致）；运行前还会校验，但不保证算法结果正确。

## 这一步在哪台机器上跑？

需要另一台机器的 GPU 时，你可能会先 SSH 登录，再进入环境启动程序。

在画布上选好提供该 Pack 的**计算设备**（Compute node），其 **Executor** 负责启动独立算法进程。

负责校验、按依赖顺序派工、管理状态和登记产物的是 **Gateway**。它可与设备服务同机部署；设备的 Node 服务与画布节点是两回事。

<figure class="concept-diagram" aria-label="浏览器画布向 Gateway 提交运行，Gateway 向设备 A 或 B 派工并接收状态；各设备在自己的工作空间存放产物">
<div class="concept-flow">
<div class="concept-card"><strong>浏览器 · 画布</strong><span>Workflow</span><span>提交运行 / 查看状态</span></div>
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 8h18m-5-5 5 5-5 5M21 16H3m5-5-5 5 5 5" /></svg>
<div class="concept-card"><strong>Gateway</strong><span>派工 / 接收状态</span><span>保存记录与产物索引</span></div>
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 8h18m-5-5 5 5-5 5M21 16H3m5-5-5 5 5 5" /></svg>
<div class="concept-devices"><div class="concept-card"><strong>设备 A · Executor</strong><span>算法进程 → 本机产物</span></div><div class="concept-card"><strong>设备 B · Executor</strong><span>算法进程 → 本机产物</span></div></div>
</div>
<figcaption>你仍然使用自己的机器和环境，只是从画布提交任务；Gateway 记下产物位置，文件留在生成它的设备上。</figcaption>
</figure>

## 跑完后，怎么找到这次结果？

翻输出目录时，你可能会问：这个文件是哪次跑的，当时用了什么参数？

在 HoloLab 中，输出成为**产物**（Artifact），关联步骤的任务记录（**Job**）和带工作流快照的运行记录（**Run**），可回查参数、设备、版本与日志。预览取决于类型与预览器，进度取决于 Pack 是否上报可解析的信息。

## 七台相机，不必手写七次调用

要给七台相机的视频分别抽帧，你可能会写一个 for 循环。

在 HoloLab 中，这组输入叫 `arrayed<T>`；Pack 声明 `arrayable` 并开启数组处理后，就按外层元素拆任务，称为 **Fan-out**。

默认一元素一分片，可调整批大小，在所选设备上按并发限制执行。全部成功后汇总输出集合，再交给下游，不会自动融合模型。

## 如果你用过 ComfyUI

用过 ComfyUI，你会熟悉节点和连线，它[也有类型检查](https://docs.comfy.org/basic-concepts/workflow)。

HoloLab 用 Pack 接入外部程序、指定执行设备，与常见的[服务端 Python 自定义节点](https://docs.comfy.org/custom-nodes/overview)不同。还需留意数组层数与轴名，以及运行、任务、产物之间的追溯关系。

接下来：[快速开始 →](quick-start.md)
