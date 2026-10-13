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

Gateway 接收任务状态并登记产物位置，实际文件留在执行设备上，供后续步骤使用或回看。

<figure class="concept-diagram" aria-label="分开部署示例：Gateway、设备 A 和设备 B 分别位于三台机器，虚线框表示机器边界。箭头表示谁主动访问谁：用户通过浏览器画布访问 Gateway；设备 A 和设备 B 分别主动建立指向 Gateway 的 WebSocket 连接。">
<div class="concept-flow concept-access">
<div class="concept-person"><strong><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM2 18v-3a4 4 0 0 1 4-4h2M10 11h12v9H10zM8 22h15M15 14l-2 2 2 2m3-4 2 2-2 2" /></svg></strong></div>
<div class="concept-connection"><span>浏览器画布</span><svg viewBox="0 0 100 24" aria-hidden="true"><path d="M2 12h96m-7-6 7 6-7 6" /></svg></div>
<div class="concept-machine"><span>网关机器</span><div class="concept-card"><strong>Gateway</strong><span>派工 / 接收状态</span><span>保存记录与产物索引</span></div></div>
<div class="concept-connection"><span>WebSocket 连接</span><svg viewBox="0 0 100 24" aria-hidden="true"><path d="M98 12H2m7-6-7 6 7 6" /></svg></div>
<div class="concept-machine"><span>机器 A</span><div class="concept-card"><strong>设备 A · Executor</strong><span>算法进程 / 本机产物</span></div></div>
<div class="concept-connection"><span>WebSocket 连接</span><svg viewBox="0 0 100 24" aria-hidden="true"><path d="M98 12H2m7-6-7 6 7 6" /></svg></div>
<div class="concept-machine"><span>机器 B</span><div class="concept-card"><strong>设备 B · Executor</strong><span>算法进程 / 本机产物</span></div></div>
</div>
<figcaption>这里以三台机器分开部署为例，虚线框表示机器边界，实线卡片表示程序实体。蓝色箭头表示主动访问方向。设备主动连接 Gateway，派工和状态回报复用这条 WebSocket 连接。产物预览另走浏览器 → Gateway → 设备文件服务；跨设备输入由消费设备直连生产设备拉取，文件服务地址须对请求方可达。</figcaption>
</figure>

Gateway 与设备服务可以部署在同一台机器，也可以分开部署。

<figure class="concept-diagram" aria-label="同机部署：用户通过浏览器画布访问 Gateway。Gateway 与设备服务 Executor 两个程序实体位于同一个机器虚线框内；设备在本机主动连接 Gateway，Gateway 在本机访问设备文件服务以提供预览。">
<div class="concept-flow concept-access concept-colocated">
<div class="concept-person"><strong><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM2 18v-3a4 4 0 0 1 4-4h2M10 11h12v9H10zM8 22h15M15 14l-2 2 2 2m3-4 2 2-2 2" /></svg></strong></div>
<div class="concept-connection"><span>浏览器画布</span><svg viewBox="0 0 100 24" aria-hidden="true"><path d="M2 12h96m-7-6 7 6-7 6" /></svg></div>
<div class="concept-machine concept-shared"><span>同一台机器 · 本机通信</span>
<div class="concept-card"><strong>Gateway</strong></div>
<div class="concept-deployment-links"><span>WebSocket</span><svg viewBox="0 0 100 24" aria-hidden="true"><path d="M98 12H2m7-6-7 6 7 6" /></svg><svg viewBox="0 0 100 24" aria-hidden="true"><path d="M2 12h96m-7-6 7 6-7 6" /></svg><span>HTTP 预览</span></div>
<div class="concept-card"><strong>设备服务</strong><span>Executor</span><span>文件服务</span></div>
</div>
</div>
<figcaption>用户仍通过浏览器画布访问 Gateway；同机部署只改变服务所在的位置。使用默认端口时，节点连接 ws://127.0.0.1:8828，文件服务监听 127.0.0.1:8829，advertised_url 可设为 http://127.0.0.1:8829。</figcaption>
</figure>

## 产物管理不再是难事

翻输出目录时，你可能会问：这个文件是哪次跑的，当时用了什么参数？

在 HoloLab 中，已登记的输出文件或目录可以在 **Artifacts** 中查找。生成产物的任务记录（Job）可用于回查参数、设备、算法版本与日志；运行记录（Run）则关联工作流快照和相关任务。预览取决于类型与预览器，进度取决于 Pack 是否上报可解析的信息。

它们如何关联、重跑时哪些结果会复用，见[产物与运行记录：找到结果的来路](run-records.md)。

## 七台相机，不必手写七次调用

要给七台相机的视频分别抽帧，你可能会写一个 for 循环。

在 HoloLab 中，这组输入叫 `arrayed<T>`；Pack 声明 `arrayable` 并开启数组处理后，就按外层元素拆任务，称为 **Fan-out**。

默认一元素一分片，可调整批大小，在所选设备上按并发限制执行。全部成功后汇总输出集合，再交给下游，不会自动融合模型。

## 如果你用过 ComfyUI

用过 ComfyUI，你会熟悉节点和连线，它也有类型检查。

HoloLab 用 Pack 接入外部程序、指定执行设备，与常见的服务端 Python 自定义节点不同。还需留意数组层数与轴名，以及运行、任务、产物之间的追溯关系。

接下来：[快速开始 →](quick-start.md)
