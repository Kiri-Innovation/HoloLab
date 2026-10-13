---
title: 介绍 HoloLab
group: 开始使用
order: 0
---
# 介绍 HoloLab

HoloLab 把视觉算法接成可运行、可检查的工作流，适合 SfM、NeRF、3DGS 等研究。把它想成**实验流水线的调度台**：画布安排步骤，Gateway 派工，计算设备执行，产物记录留下每次实验的来路。

## 画布上的三个概念

**Workflow**（工作流）是画布上由节点和连线组成的一张图。**Node**（算法节点）代表一个步骤：输入／输出端口接数据，参数控制算法，状态显示任务是否等待、运行或失败。

**Pack**（算法包）把已有算法接进来：用 manifest 声明 `inputs`、`outputs`、`params`、`runtime` 和 `exec`。设备扫描到 Pack 后，它就成为可选的节点类型；实际执行命令启动独立进程，环境和数据仍需事先准备。

## 连线是有类型的接口

**Link**（连线）把上游产物交给下游输入。端口必须声明机器可读的类型；拖线时就检查，不兼容会拒绝并提示原因，运行前还会再校验。检查包括数据类型、数组层数，以及默认要求一致的已命名轴；Pack 可明确放宽轴名检查。

<figure class="concept-diagram" aria-label="真实 Pack 示例：视频来源输出 video-source，抽帧接收它并输出按 frame 排列的图片集合">
<div class="concept-flow">
<div class="concept-card"><strong>视频来源</strong><code>single-video-source</code><span>输出：video-source</span></div>
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h17m-6-6 6 6-6 6" /></svg>
<div class="concept-card"><strong>抽帧</strong><code>frame-extraction</code><span>输入：video-source</span></div>
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h17m-6-6 6 6-6 6" /></svg>
<div class="concept-card"><strong>图片集合 · 产物</strong><code>arrayed[frame]&lt;image&gt;</code><span>每个元素是一张图片</span></div>
</div>
<figcaption>不能把 video-source 直接接到 image 输入；图片集合也不等于单张图片。类型通过不保证数据质量或算法成功。</figcaption>
</figure>

## 谁管理，谁执行？

**Gateway** 是协调服务：校验工作流、按依赖顺序派发任务、管理状态并登记产物。**计算设备**（Compute node）运行 Node 服务，其中的 **Executor** 启动算法进程；它与画布上的算法节点是两种实体。

<figure class="concept-diagram" aria-label="浏览器画布向 Gateway 提交运行，Gateway 向设备 A 或 B 派工并接收状态；各设备在自己的工作空间存放产物">
<div class="concept-flow">
<div class="concept-card"><strong>浏览器 · 画布</strong><span>Workflow</span><span>提交运行 / 查看状态</span></div>
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 8h18m-5-5 5 5-5 5M21 16H3m5-5-5 5 5 5" /></svg>
<div class="concept-card"><strong>Gateway</strong><span>派工 / 接收状态</span><span>保存记录与产物索引</span></div>
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 8h18m-5-5 5 5-5 5M21 16H3m5-5-5 5 5 5" /></svg>
<div class="concept-devices"><div class="concept-card"><strong>设备 A · Executor</strong><span>算法进程 → 本机产物</span></div><div class="concept-card"><strong>设备 B · Executor</strong><span>算法进程 → 本机产物</span></div></div>
</div>
<figcaption>服务可在同机，也可跨机器。文件留在生成它的设备工作空间；Gateway 保存索引，下游跨设备使用时拉取数据。</figcaption>
</figure>

每步在 **Compute node** 中选择提供该 Pack 的设备。当前整图按依赖顺序逐步执行；图示不表示各分支会自动并行。

## 一次运行留下什么？

**Run** 是一次运行记录，关联当时的工作流快照；**Job** 是步骤或分片的任务记录，带参数、设备、状态和日志；**Artifact**（产物）是输出文件或目录，可追溯到任务、运行和算法版本。预览取决于类型与预览器；进度显示取决于 Pack 是否提供可解析的信息。

## 一组输入：Arrayed / Fan-out

`arrayed<T>` 表示一组 T，并不自动拆任务。Pack 声明 `arrayable` 且节点开启数组处理后，才按外层元素拆分：默认 N 个元素生成 N 个分片，设置批大小后可每片处理多个元素。分片在所选设备上执行，并发受配置限制；全部成功后按输出端口汇总成集合，再交给下游，**不等于自动融合模型**。

## 从 ComfyUI 过来

两者都用节点图，[ComfyUI 也检查连线类型](https://docs.comfy.org/basic-concepts/workflow)。HoloLab 需要多理解四件事：**Pack 的 manifest 接入外部算法进程；Gateway 与设备分工、每步指定执行位置；类型约束细到数组层数与轴名；产物关联任务和运行快照**。作为对照，ComfyUI 的常见自定义节点由[服务端 Python 类实现](https://docs.comfy.org/custom-nodes/overview)。

接下来：[快速开始 →](quick-start.md)
