---
title: 节点、数据类型与连线
group: 详细功能介绍
order: 27
---
# 节点、数据类型与连线

把两个算法接起来，不能只看输入输出的名字是否相似。HoloLab 会先检查声明的数据类型，再接受连线。

## 按输入和输出封装算法

算法包（Pack）声明算法的输入、输出和参数，节点把这些接口呈现在画布上。例如，`frame-extraction` 从 `video_dir` 接收视频目录，通过 `frame_sequence` 输出图像序列。参数控制算法如何运行，修改方式见[第一个工作流](workflows.md)。

## 端口带着类型声明

节点上可连线的位置叫**端口（Port）**：左侧的输入端口接收数据，右侧的输出端口交出数据。

每个端口都有机器可读的声明：类型标签说明是什么数据，数组结构和轴标签说明集合如何组织。在这个例子里，`video_dir` 接收 `video-source`；`frame_sequence` 输出的是沿 `frame` 轴排列的 `image` 数组。端口名是接口的名称，不等于类型。

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/ports-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/ports-light.webp" alt="STG 工作流中，视频源的输出连接到 frame-extraction 的 video_dir 输入端口。" width="1462" height="280" loading="lazy" decoding="async" /></picture>
<figcaption>视频源通过兼容的端口接入 frame-extraction。端口名说明用途，类型声明决定能否连接。</figcaption>
</figure>

从输出端口拖线到输入端口即可连接。拖动时，HoloLab 就会检查兼容性；松手时，不兼容的连线会被拒绝。检查包括类型标签、数组层数，以及声明要求匹配的轴名。单张图像和图像集合是不同的数据形态，名字相近也不代表能接上。

## 连线表达数据依赖

运行时，系统沿连线把上游输出的产物引用交给下游输入。要通过 agent 查看这些上下文，见[一切面向 Agent](agent-oriented.md)。

类型检查保证的是声明的数据形态兼容，不保证算法结果正确。

接下来可以[查看结果](results.md)。想把自己的算法接成节点，见[编写 Pack](/docs/build/writing-a-pack)。
