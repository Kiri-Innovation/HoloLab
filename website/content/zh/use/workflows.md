---
title: 第一个工作流
group: 快速开始
order: 20
---
# 第一个工作流

第一次使用，先选择已经在计算设备上准备好环境和数据的 Pack。可以先运行一个不需要上游输入的节点，确认执行正常，再接下游步骤。

## 新建与添加节点

1. 在工作流列表点击 **+ New workflow**；空列表也有 **+ Create your first workflow** 入口。
2. 在画布顶部输入工作流名称。
3. 在 **Packs** 面板通过 **Search packs…** 搜索算法，将对应的算法节点添加到画布。

列表为空时，先检查设备是否在线，以及设备设置里的 **Pack sources** 是否包含 manifest 所在位置。搜索只筛选已经发现的 Pack，不会下载或安装算法。

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/add-node-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/add-node-light.webp" alt="Packs 中的 frame-extraction 与 STG 画布上的同名节点，局部截图并列；未执行添加操作。" width="1400" height="390" loading="lazy" decoding="async" /></picture>
<figcaption>Packs 中的 frame-extraction 与 STG 画布上的同名节点，局部截图并列；未执行添加操作。</figcaption>
</figure>

## 配置参数和设备

选中画布上的算法节点，在右栏 **Inspector** 标签页的 **Parameters** 修改参数，在 **Run on** 段选择提供该 Pack 的计算设备。不要留下 `unassigned`：执行前会检查设备分配。

不同 Pack 提供不同参数。路径类参数需要按算法实际运行的设备填写；浏览器所在电脑的路径不一定能被计算设备访问。

## 连线

从上游节点的输出端口拖到下游节点的输入端口。拖动时会显示是否可以连接；失败提示会给出预期类型、实际类型或不匹配原因。

检查的不只是类型名称，也包括数组层数和适用的轴名规则。例如，单张图片与图片集合不是同一种输入。不要只根据端口名称相似就判断可连接。

把所需的必填输入接好，保持流程没有循环。输入含义和参数约束仍以所选算法为准。

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/ports-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/ports-light.webp" alt="STG 链中视频来源与抽帧节点的端口和已有连线；未改动连接。" width="1462" height="280" loading="lazy" decoding="async" /></picture>
<figcaption>STG 链中视频来源与抽帧节点的端口和已有连线；未改动连接。</figcaption>
</figure>

## 等保存完成，再运行

画布修改会自动保存，顶部有保存状态提示。等状态显示 **Saved** 后，再点击右上角 **Run**。若保存失败，先点击 **Save failed · Retry** 重试；如果提示另一个页面修改了工作流，先处理冲突再运行。

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/run-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/run-light.webp" alt="顶部运行工具栏的局部截图；Run 按钮未点击。" width="566" height="96" loading="lazy" decoding="async" /></picture>
<figcaption>顶部运行工具栏的局部截图；Run 按钮未点击。</figcaption>
</figure>

执行前会校验 Pack、设备、必填输入和连线。如果弹出 **Cannot run**，根据节点或连线对应的错误逐项处理。

运行提交后，后续草稿修改不会改变已提交任务。看到“正在使用旧参数运行”时，先确认当前任务的情况，再决定是否停止并用新配置运行。

## 跟踪和停止任务

画布节点与底部 dock 的 **Jobs** 标签页会显示状态；进度取决于 Pack 是否提供了可解析的进度信息。任务行提供查看日志入口。

需要停止时，使用任务行的停止操作；有确认弹窗时核对范围后再确认。停止不代表成功完成，也不会自动补齐未生成的输出。

完成后继续[查看结果](results.md)。遇到失败或设备失联时，查看[排错](troubleshooting.md)。
