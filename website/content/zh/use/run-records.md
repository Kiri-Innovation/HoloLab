---
title: 产物与运行记录：找到结果的来路
group: 运行与结果
order: 35
---
# 产物与运行记录：找到结果的来路

用脚本跑完一条链，你可能留下几个输出目录，再靠日志和目录名回忆它们是怎么来的。在 HoloLab 中，文件或目录登记为产物，任务记录保留执行细节，运行记录把工作流快照与相关任务联系起来。

## 先跑一条三步骤的链

以“抽帧 → 重建 → 导出”为例，先看首次完整执行、无复用、无数组分片的情况。假设三个步骤都成功，并且每步各登记一个输出目录，就会得到 **1 个 Run、3 个 Job、3 个 Artifact**。

你原本需要记住“这几个目录是一轮跑出来的”；现在可以从同一条**运行记录**（Run）回看工作流快照和相关任务。代码中这条记录以 **Snapshot** 表示；它属于一个 Workflow，并保留当时的节点图，和你继续编辑的草稿区分开。

<figure class="concept-diagram" aria-label="首次执行的记录关系：Run R1 分别关联抽帧任务 J1、重建任务 J2、导出任务 J3；三个任务分别产出图片目录 A1、重建目录 A2、导出目录 A3。">
<div class="concept-records">
<div class="concept-card concept-run"><strong>R1</strong><span>Run</span></div>
<div class="concept-record-link"><span>关联</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-card"><strong>J1 · Job</strong><span>抽帧</span></div>
<div class="concept-record-link"><span>产出</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-card"><strong>A1</strong><span>图片目录</span></div>
<div class="concept-record-link"><span>关联</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-card"><strong>J2 · Job</strong><span>重建</span></div>
<div class="concept-record-link"><span>产出</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-card"><strong>A2</strong><span>重建目录</span></div>
<div class="concept-record-link"><span>关联</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-card"><strong>J3 · Job</strong><span>导出</span></div>
<div class="concept-record-link"><span>产出</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-card"><strong>A3</strong><span>导出目录</span></div>
</div>
<figcaption>A1、A2、A3 是三个 Artifact。这里的箭头表示记录关联与产出，不是网络访问方向。</figcaption>
</figure>

## 一个步骤跑过，留下什么？

翻日志查“重建用了什么参数”，对应的是这一步的**任务记录（Job）**，而不是画布上的节点本身。普通节点的一次执行会创建一个 Job，记录参数、算法版本、输入引用、执行设备和状态；再次执行会留下新的 Job。

输出目录则是**产物（Artifact）**。系统用一个产物引用 **Handle** 记录它的编号、所在设备、路径、输出端口和生产它的 Job；下游任务通过输入引用使用它，实际文件仍在执行设备上。一个登记的目录可以包含很多文件，所以产物数量不等于文件数量，也不固定等于节点数量。

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/preview-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/preview-light.webp" alt="STG 链中 colmap-triangulate 的真实点云预览；帧名与产物引用已隐藏，未展示原始影像。" width="704" height="1030" loading="lazy" decoding="async" /></picture>
<figcaption>STG 链中 colmap-triangulate 的真实点云预览；帧名与产物引用已隐藏，未展示原始影像。</figcaption>
</figure>

## 只重跑最后一步

如果你只想调整导出参数，通常不想把抽帧和重建再跑一次。沿用前两步结果、重新执行最后一步时，可以产生新运行记录 R2：它关联旧的 J1、J2，以及新的导出任务 J4；J4 产出新目录 A4，R1 仍关联原来的 J1、J2、J3。

<figure class="concept-diagram" aria-label="三列记录关系：左列 R1、R2；中列唯一的 J1、J2、J3、J4；右列 A1、A2、A3、A4。R1 关联 J1、J2、J3，R2 关联 J1、J2、J4。每个 Job 产出同编号的 Artifact；两个 Run 共享 J1、J2 及其产物 A1、A2，没有复制。">
<div class="concept-records concept-reuse">
<div class="concept-card"><strong>R1</strong><span>原运行</span></div>
<div class="concept-card"><strong>R2</strong><span>新运行</span></div>
<div class="concept-reuse-lines"><svg viewBox="0 0 120 420" preserveAspectRatio="none" aria-hidden="true"><path d="M2 102 C42 102 70 41 118 41 m-6-5 6 5-6 5" /><path d="M2 102 C42 102 70 149 118 149 m-6-5 6 5-6 5" /><path d="M2 102 C42 102 70 264 118 264 m-6-5 6 5-6 5" /><path d="M2 318 C60 318 48 55 118 55 m-6-5 6 5-6 5" /><path d="M2 318 C60 318 48 163 118 163 m-6-5 6 5-6 5" /><path d="M2 318 C42 318 70 372 118 372 m-6-5 6 5-6 5" /></svg></div>
<div class="concept-reuse-jobs">
<div class="concept-card"><strong>J1 · 抽帧</strong><span>共享任务</span></div>
<div class="concept-card"><strong>J2 · 重建</strong><span>共享任务</span></div>
<div class="concept-card"><strong>J3 · 导出</strong><span>原任务</span></div>
<div class="concept-card"><strong>J4 · 导出</strong><span>新任务</span></div>
</div>
<div class="concept-reuse-outputs">
<div class="concept-record-link"><span>产出</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-record-link"><span>产出</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-record-link"><span>产出</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
<div class="concept-record-link"><span>产出</span><svg viewBox="0 0 40 20" aria-hidden="true"><path d="M2 10h36m-6-6 6 6-6 6" /></svg></div>
</div>
<div class="concept-reuse-artifacts">
<div class="concept-card"><strong>A1</strong><span>图片目录</span></div>
<div class="concept-card"><strong>A2</strong><span>重建目录</span></div>
<div class="concept-card"><strong>A3</strong><span>原导出</span></div>
<div class="concept-card"><strong>A4</strong><span>新导出</span></div>
</div>
</div>
<figcaption>左侧箭头表示 Run 关联 Job：R1 关联 J1、J2、J3，R2 关联 J1、J2、J4。交叉处不相连，J1、J2 各接收两个 Run 的箭头，记录及其产物 A1、A2 没有复制。右侧箭头表示 Job 产出 Artifact：J1 → A1、J2 → A2、J3 → A3、J4 → A4。这里均不是网络访问方向。</figcaption>
</figure>

这时共有 **2 个 Run、4 个不同的 Job、4 个产物**，不是每个 Run 各自拥有三份互不相干的任务。Run 与 Job 是可复用的关联关系；产物记录生产它的 Job，再由这个 Job 追溯到关联的 Run。

也不是每点一次运行就新建一个 Run：继续执行尚未填入当前记录的步骤时，可以沿用原记录。开启数组处理后还会出现父任务和分片任务，所以前面的“3 节点、3 Job”只适用于那个简单例子。

## 想查什么，去哪里看？

不用先记住所有编号，从你现在的问题出发即可：

| 想查什么 | 界面路径 | 能看到什么 |
| --- | --- | --- |
| 当时用了哪张图、哪些步骤跑过 | 打开工作流 → 右栏 **Runs** 标签页 → 点击记录 | 工作流快照与相关任务；历史快照是只读视图 |
| 哪一步失败、日志说了什么 | 工作流 → 底部 dock → **Jobs** → **Recent jobs** → **查看日志 / 查看失败日志** | 对应 Job 的日志与失败信息 |
| 输出是什么、还在不在 | 工作流列表页 → **Artifacts** → 查找产物 | 来源算法、输出端口与文件路径；可检查文件是否存在 |
| 想直接看结果 | 画布 → 节点的产物预览 | 受支持类型的预览；没有预览器不代表没生成文件 |

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/runs-jobs-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/runs-jobs-light.webp" alt="STG 的右栏 Runs 标签页与底部 Jobs 标签页局部截图并列；两者实际位于界面的不同区域，采集时 Recent jobs 为空。时间与记录标识已隐藏。" width="1616" height="714" loading="lazy" decoding="async" /></picture>
<figcaption>STG 的右栏 Runs 标签页与底部 Jobs 标签页局部截图并列；两者实际位于界面的不同区域，采集时 Recent jobs 为空。时间与记录标识已隐藏。</figcaption>
</figure>

**当前限制**：按 Run 筛选产物时，筛选依据仍是生产 Job 首次所属的运行记录，因此可能漏掉从旧 Run 继承的 Job 产物。找不到时，可以取消 Run 筛选，按来源算法和文件路径核对，或回看原来的 Run；不要仅凭筛选结果判断文件已经丢失。

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/artifacts-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/artifacts-light.webp" alt="Artifacts 页已筛选到 STG 工作流；实际路径、时间和工作流名称已隐藏。" width="3200" height="1140" loading="lazy" decoding="async" /></picture>
<figcaption>Artifacts 页已筛选到 STG 工作流；实际路径、时间和工作流名称已隐藏。</figcaption>
</figure>

预览取决于产物类型和预览器；进度取决于 Pack 是否上报可解析的信息。预览、文件位置与清理操作继续看[查看结果](results.md)。
