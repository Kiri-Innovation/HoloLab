---
title: 介绍 HoloLab
group: 开始使用
order: 0
---
# 介绍 HoloLab

做视觉实验时，你可能要把抽帧、重建、训练几个步骤串起来，反复改参数、看结果。HoloLab 像一张可以派工的实验流程图，让你在画布上安排这些步骤。

## 工作流：从管线变为画布上的节点图

我们常把多个算法步骤写进一个 Python 或 Shell 脚本，按顺序执行；调整管线时，就去修改这个脚本。

在 HoloLab 中，你在画布上用节点和连线组织这些步骤，编辑这张**节点图**（node graph）就是在编辑工作流。算法本身仍由原来的程序执行，你可以通过编辑这张图调整它们的连接方式。

## 节点：原子化的算法片段

假设你已经有一个视频，平时会先调用抽帧脚本，再把输出交给下一步；这个算法可能是一个 Python 程序，也可能靠 Shell 命令启动。

在 HoloLab 中，抽帧这样的独立步骤被封装成一个**节点**（Node），作为可组合的原子化算法片段。节点通过**端口**接收和交出数据，输入输出遵循统一的、机器可读的接口声明，让下一步知道会接到什么。

给已有算法加一个配置文件 manifest，声明输入输出端口、参数、运行环境和执行命令，就把它接成了**算法包**（Pack）。设备扫描后，画布才能添加对应节点；环境和数据仍需先准备。

选中节点可以修改参数、查看运行状态。

## 连线：告诉系统如何在算法间传递数据

平时，你会把上一步的输出路径写进下一步的命令行参数或配置里；路径写错或数据类型不对，往往跑起来才发现。

在 HoloLab 中，从输出端口拉到输入端口的一条**连线**（Link），就记录了“这个输出交给那个输入”。它表达的是步骤之间的数据依赖关系。

运行时，系统按连线把上游的产物引用交给下游任务，执行设备再将引用解析为算法可用的本地路径；跨设备时会先拉取文件。文件本身并不是沿着画布上的线流动的。

只有类型兼容的端口才能连接，接错会在拖线时被拒绝。

<figure class="concept-diagram" aria-label="把视频交给抽帧步骤，得到一组图片：连线连接上游输出端口和下游输入端口">
<div class="concept-flow">
<div class="concept-card"><strong>视频来源</strong><span>输出端口：视频</span></div>
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h17m-6-6 6 6-6 6" /></svg>
<div class="concept-card"><strong>抽帧</strong><span>输入端口：视频</span><span>输出端口：图片集合</span></div>
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h17m-6-6 6 6-6 6" /></svg>
<div class="concept-card"><strong>得到一组图片</strong><span>每个元素是一张图片</span></div>
</div>
<figcaption>图中的箭头记录输入输出的对应关系；实际文件由执行设备按需读取或拉取。</figcaption>
</figure>

## HoloLab 如何运行

用户通过浏览器画布提交工作流。接收并校验这张图、解析步骤间依赖，再按依赖顺序派发任务的协调服务，是 **Gateway**。

设备扫描到 Pack 并上线后，你就能为相应步骤选择这台**计算设备**（Compute node）。任务到达设备后，由 **Executor** 在 Pack 声明、设备已配置好的环境中启动独立算法进程。

Gateway 接收任务状态并登记产物位置，实际文件留在执行设备上，供后续步骤使用或回看。Gateway 与设备服务可以部署在同一台机器，也可以分开部署。

<figure class="concept-diagram" aria-label="用户通过浏览器画布向 Gateway 提交工作流并查看状态；Gateway 向设备 A 或 B 的 Executor 派发任务，产物存放在执行设备上">
<div class="concept-flow">
<div class="concept-card"><strong>用户</strong><span>提交运行 / 查看状态</span></div>
<div class="concept-connection"><span>浏览器画布</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 8h18m-5-5 5 5-5 5M21 16H3m5-5-5 5 5 5" /></svg></div>
<div class="concept-card"><strong>Gateway</strong><span>派工 / 接收状态</span><span>保存记录与产物索引</span></div>
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 8h18m-5-5 5 5-5 5M21 16H3m5-5-5 5 5 5" /></svg>
<div class="concept-devices"><div class="concept-card"><strong>设备 A · Executor</strong><span>算法进程 → 本机产物</span></div><div class="concept-card"><strong>设备 B · Executor</strong><span>算法进程 → 本机产物</span></div></div>
</div>
<figcaption>浏览器画布是用户与 Gateway 交互的界面；算法由设备上的 Executor 执行，Gateway 保存运行记录与产物索引。</figcaption>
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
