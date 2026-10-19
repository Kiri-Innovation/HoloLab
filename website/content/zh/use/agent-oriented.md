---
title: 一切面向 Agent
group: 快速开始
order: 25
---
# 一切面向 Agent

你可以在画布上工作，遇到问题时把具体对象交给 agent，不必先在对话里重新描述现场。HoloLab 给人提供可视化工作区，也让 agent 通过结构化接口读取同一份工作流和执行记录。

## 一切皆可引用

不用描述“刚才失败的那个节点”，直接使用它的引用入口，把 `hololab://` 引用交给 agent。引用标识的是实例内的对象，因此也要把实例地址告诉 agent。

<div class="no-flops-only">

<figure class="doc-screenshot">
<div class="annotated-canvas"><picture><source srcset="/images/docs/reference-entry-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/reference-entry-light.webp" alt="高亮框标出“复制引用信息”：STG 节点标题右侧、运行按钮左边的剪贴板图标。" width="744" height="280" loading="lazy" decoding="async" /></picture><div class="canvas-annotations" aria-hidden="true"><span class="canvas-region region-reference"><span class="reference-label">复制引用信息</span></span></div></div>
<figcaption>高亮框标出“复制引用信息”：STG 节点标题右侧、运行按钮左边的剪贴板图标。</figcaption>
</figure>

点击高亮入口会复制引用文本，供你粘贴到与 agent 的对话中。复制的是 `hololab://` 引用及可选注释，不包含 Markdown 摘要。下面是该节点的复制结果（工作流 ID 已脱敏）：

```text
hololab://graph-node/<workflow-id>/fx  # frame-extraction
```

</div>

<div class="flops-only">

<figure class="doc-screenshot">
<div class="annotated-canvas"><picture><source srcset="/images/docs/reference-flops-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/reference-flops-light.webp" alt="示意图：模拟 Flops 环境下的“引用到 Flops”。成对引号图标与产品源码一致；普通浏览器使用剪贴板图标。本图并非 Flops 真机截图。" width="744" height="280" loading="lazy" decoding="async" /></picture><div class="canvas-annotations" aria-hidden="true"><span class="canvas-region region-reference"><span class="reference-label">引用到 Flops</span></span></div></div>
<figcaption>示意图：模拟 Flops 环境下的“引用到 Flops”。成对引号图标与产品源码一致；普通浏览器使用剪贴板图标。本图并非 Flops 真机截图。</figcaption>
</figure>

在 Flops 内嵌浏览器中，引用会插入对话输入框。插入的引用附带一份标记为 `hololab.reference-snapshot/v1` 的 Markdown 摘要，分为 identity、state、context 和 navigation 四部分。它记录的是引用时的情况；agent 可以重新解析引用，获取当前数据。

</div>

目前支持的引用对象，见文末的[支持范围速查表](#支持范围速查表)。

## 一切皆可被 agent 读取

agent 可以调用 `GET /api/resolve?ref=hololab://…` 读取对象，再沿返回的关联链接继续查。对于画布节点，响应包含已保存的参数、上下游连接，以及存在时的最近执行归属和输出 Handle。

下面摘自这条 STG 工作流中一个已完成 Job 的真实解析响应，仅保留部分字段；记录 ID 已替换为占位符。`ref` 标识对象，`resource` 给出任务状态与算法上下文，`related` 提供后续读取入口。

```json
{
  "kind": "job",
  "ref": "hololab://job/<job-id>",
  "resource": {
    "job_id": "<job-id>",
    "graph_node_id": "und",
    "algorithm_name": "image-undistort",
    "algorithm_version": "0.5.0",
    "state": "done",
    "progress": null
  },
  "related": {
    "log": "/api/jobs/<job-id>/log",
    "workflow": "/api/workflows/<workflow-id>",
    "snapshot": "/api/snapshots/<snapshot-id>"
  }
}
```

解析接口返回 JSON，引用中的四段式 Markdown 摘要由它生成。任务日志可从 `/api/jobs/<id>/log` 读取，运行快照可从 `/api/snapshots/<id>` 读取。agent 因而可以调查失败步骤，不必从界面像素里辨认状态；但要判断渲染效果好不好，仍然需要看产物。

## 一切包含在技能中

仓库中的 [skill/SKILL.md](https://github.com/Kiri-Innovation/HoloLab/blob/main/skill/SKILL.md) 是 HoloLab 的操作指南，涵盖解析引用、编辑和运行工作流、等待结果、读取产物与日志，以及取消任务。

准备好实例地址，把下面这段话复制给能访问该实例的 agent；先将 `<URL>` 替换为你的地址。链接指向纯文本技能文件，便于 agent 直接读取。

<div class="copyable-code">

```text
请读取 https://raw.githubusercontent.com/Kiri-Innovation/HoloLab/main/skill/SKILL.md
并把它作为操作 HoloLab 的技能指南。我的实例地址是 <URL>。
行动前先解析我粘贴的 hololab:// 引用；使用长轮询等待运行结果；
建议修改前先读取相关日志。涉及修改或运行时，请遵循我给出的任务范围。
```

</div>

等待运行完成、汇总各任务状态这类细节，技能里已有现成步骤，不需要你自己写轮询。

这让 agent 在当前任务中读取并使用操作指南，不等于安装到它的持久技能库。目前实例没有自动下载或安装技能的端点。这份技能是附带后续文档入口的操作指南，并非把所有手册都打包在一个文件里。

## 先试一个只读问题

把实例地址、技能文件和一条 Job 引用交给能访问该实例 HTTP 接口的 agent，问它：“这个任务为什么失败？先读参数和日志，再建议怎么改。”第一次交接就有了具体对象，也不需要另起一次运行。

实体概念见[介绍 HoloLab](/docs/introduction)和[产物与运行记录](/docs/use/run-records)。接下来：[第一个工作流 →](/docs/use/workflows)。

## 支持范围速查表

目前已支持以下对象；说明列是解析引用后可读取的信息。

| 对象 | 引用格式 | 可读取的信息 |
|---|---|---|
| 工作流 | `hololab://workflow/<id>` | 草稿节点图、参数与最近运行摘要。 |
| 工作流节点 | `hololab://graph-node/<workflow-id>/<node-id>` | 参数、可用时的端口类型、上下游与最近任务产物。 |
| 运行记录（快照） | `hololab://run/<id>` | 该次运行的节点图、关联任务与等待结果的入口。 |
| 任务记录 | `hololab://job/<id>` | 状态、进度、失败原因、参数、输入输出与日志入口。 |
| 产物引用 | `hololab://handle/<id>` | 类型标签、大小、输出端口、文件与摘要入口。 |
| 算法包 | `hololab://pack/<name>@<version>` | 输入输出、参数声明与提供该包的设备。 |
| 计算设备 | `hololab://node/<id>` | 设备信息、GPU 与可用 Pack。 |
| 工作流列表 | `hololab://workflows` | 工作流数量、名称、节点数与最近运行摘要。 |
| 产物汇总 | `hololab://artifacts` | 产物数量、总大小、按工作流汇总与列表入口。 |
| 资源库资源 | `hololab://library-resource/<id>` | 当前版本、类型、大小、设备分布与传输状态。 |
| 资源库文件夹 | `hololab://library-folder/<id>` | 名称、父文件夹与资源列表入口。 |

`<id>` 是对象 ID；资源库根文件夹用 `hololab://library-folder/root`。运行快照使用 `run`，单个产物使用 `handle`；`snapshot` 和 `artifact` 不是支持的 kind。
